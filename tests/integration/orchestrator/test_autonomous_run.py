"""Integration test exercising the orchestrator command runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from api_service.db import models as db_models
from moonmind.workflows.orchestrator.action_plan import generate_action_plan
from moonmind.workflows.orchestrator.command_runner import (
    AllowListViolation,
    CommandRunner,
)
from moonmind.workflows.orchestrator.service_profiles import ServiceProfile
from moonmind.workflows.orchestrator.storage import ArtifactStorage


@pytest.mark.integration
def test_autonomous_run_generates_artifacts(tmp_path: Path) -> None:
    """Simulate an autonomous run over a temporary git repository."""

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "MoonMind CI"], cwd=repo_path, check=True
    )
    requirements = repo_path / "requirements.txt"
    requirements.write_text("flask==1.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "requirements.txt"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
    )

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("requirements.txt",),
        healthcheck=None,
    )
    plan = generate_action_plan("Update dependency", profile)

    build_params: dict[str, object] | None = None
    restart_params: dict[str, object] | None = None
    verify_params: dict[str, object] | None = None
    for step in plan.steps:
        parameters = dict(step.parameters)
        if step.name == db_models.OrchestratorPlanStep.BUILD:
            parameters["command"] = ["echo", "build"]
            build_params = parameters
        elif step.name == db_models.OrchestratorPlanStep.RESTART:
            parameters["command"] = ["echo", "restart"]
            restart_params = parameters
        elif step.name == db_models.OrchestratorPlanStep.VERIFY:
            parameters.pop("healthcheck", None)
            verify_params = parameters

    storage = ArtifactStorage(tmp_path / "artifacts")
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    messages: list[str] = []
    artifact_paths: list[str] = []

    # Analyze step produces overview log.
    result = runner.analyze(plan.steps[0].parameters)
    messages.append(result.message)
    artifact_paths.extend(artifact.path for artifact in result.artifacts)

    # Mutate dependency manifest to simulate orchestrator patch.
    requirements.write_text("flask==1.1\n", encoding="utf-8")
    result = runner.patch(plan.steps[1].parameters)
    messages.append(result.message)
    artifact_paths.extend(artifact.path for artifact in result.artifacts)

    # Build, restart, and verify steps run mocked commands.
    for step in plan.steps[2:5]:
        if step.name == db_models.OrchestratorPlanStep.BUILD and build_params:
            step_result = runner.build(build_params)
        elif step.name == db_models.OrchestratorPlanStep.RESTART and restart_params:
            step_result = runner.restart(restart_params)
        elif step.name == db_models.OrchestratorPlanStep.VERIFY and verify_params:
            step_result = runner.verify(verify_params)
        else:
            step_result = runner.verify(step.parameters)
        messages.append(step_result.message)
        artifact_paths.extend(artifact.path for artifact in step_result.artifacts)

    artifact_root = storage.ensure_run_directory(run_id)
    expected_artifacts = {
        "analyze.log",
        "patch.diff",
        "patch.log",
        "build.log",
        "restart.log",
        "verify.log",
    }
    materialized = {Path(path).name for path in artifact_paths}
    assert expected_artifacts.issubset(materialized)

    for name in expected_artifacts:
        assert (artifact_root / name).exists(), f"Missing artifact {name}"

    assert messages[0].startswith("Analysis complete")
    assert "Patched files" in messages[1]
    assert messages[2:]  # Remaining steps recorded messages


@pytest.mark.integration
def test_patch_step_rejects_untracked_outside_allowlist(tmp_path: Path) -> None:
    """Patch step should enforce allow list against untracked files."""

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "MoonMind CI"], cwd=repo_path, check=True
    )

    requirements = repo_path / "requirements.txt"
    requirements.write_text("flask==1.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "requirements.txt"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
    )

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("requirements.txt",),
        healthcheck=None,
    )
    plan = generate_action_plan("Update dependency", profile)
    storage = ArtifactStorage(tmp_path / "artifacts")
    runner = CommandRunner(run_id=uuid4(), profile=profile, artifact_storage=storage)

    # Simulate orchestrator command writing an unauthorized file without tracking it.
    rogue_script = repo_path / "scripts" / "exploit.sh"
    rogue_script.parent.mkdir(parents=True, exist_ok=True)
    rogue_script.write_text("#!/bin/sh\necho exploit\n", encoding="utf-8")

    requirements.write_text("flask==1.2\n", encoding="utf-8")

    with pytest.raises(AllowListViolation) as excinfo:
        runner.patch(plan.steps[1].parameters)

    assert "scripts/exploit.sh" in str(excinfo.value)


@pytest.mark.integration
def test_patch_step_rejects_staged_outside_allowlist(tmp_path: Path) -> None:
    """Patch step should enforce allow list against staged files."""

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "MoonMind CI"], cwd=repo_path, check=True
    )

    allowed = repo_path / "requirements.txt"
    allowed.write_text("flask==1.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "requirements.txt"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
    )

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("requirements.txt",),
        healthcheck=None,
    )
    plan = generate_action_plan("Update dependency", profile)
    storage = ArtifactStorage(tmp_path / "artifacts")
    runner = CommandRunner(run_id=uuid4(), profile=profile, artifact_storage=storage)

    rogue_script = repo_path / "scripts" / "exploit.sh"
    rogue_script.parent.mkdir(parents=True, exist_ok=True)
    rogue_script.write_text("#!/bin/sh\necho exploit\n", encoding="utf-8")
    subprocess.run(["git", "add", "scripts/exploit.sh"], cwd=repo_path, check=True)

    with pytest.raises(AllowListViolation) as excinfo:
        runner.patch(plan.steps[1].parameters)

    assert "scripts/exploit.sh" in str(excinfo.value)


@pytest.mark.integration
def test_patch_step_honors_plan_allowlist_override(tmp_path: Path) -> None:
    """Patch step must respect allowlist overrides captured in the plan."""

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "MoonMind CI"], cwd=repo_path, check=True
    )

    requirements = repo_path / "requirements.txt"
    requirements.write_text("flask==1.0\n", encoding="utf-8")
    app_py = repo_path / "src" / "app.py"
    app_py.parent.mkdir()
    app_py.write_text("print('hello')\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", "requirements.txt", "src/app.py"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_path,
        check=True,
        stdout=subprocess.PIPE,
    )

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("requirements.txt", "src/*.py"),
        healthcheck=None,
    )
    plan = generate_action_plan("Update dependency", profile)
    storage = ArtifactStorage(tmp_path / "artifacts")
    runner = CommandRunner(run_id=uuid4(), profile=profile, artifact_storage=storage)

    # Modify app.py which is normally allowed by the service profile but will be
    # rejected when the plan narrows the allow list to requirements.txt only.
    app_py.write_text("print('updated')\n", encoding="utf-8")

    patch_parameters = dict(plan.steps[1].parameters)
    patch_parameters["allowlist"] = ["requirements.txt"]

    with pytest.raises(AllowListViolation) as excinfo:
        runner.patch(patch_parameters)

    assert "src/app.py" in str(excinfo.value)
