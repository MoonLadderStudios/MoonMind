"""Integration test exercising the orchestrator command runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from api_service.db import models as db_models
from moonmind.workflows.orchestrator.action_plan import generate_action_plan
from moonmind.workflows.orchestrator.command_runner import CommandRunner
from moonmind.workflows.orchestrator.service_profiles import ServiceProfile
from moonmind.workflows.orchestrator.storage import ArtifactStorage


@pytest.mark.integration
def test_autonomous_run_generates_artifacts(tmp_path: Path) -> None:
    """Simulate an autonomous run over a temporary git repository."""

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "MoonMind CI"], cwd=repo_path, check=True)
    requirements = repo_path / "requirements.txt"
    requirements.write_text("flask==1.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "requirements.txt"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, stdout=subprocess.PIPE)

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("requirements.txt",),
        healthcheck=None,
    )
    plan = generate_action_plan("Update dependency", profile)

    for step in plan.steps:
        if step.name == db_models.OrchestratorPlanStep.BUILD:
            step.parameters["command"] = ["echo", "build"]
        elif step.name == db_models.OrchestratorPlanStep.RESTART:
            step.parameters["command"] = ["echo", "restart"]
        elif step.name == db_models.OrchestratorPlanStep.VERIFY:
            step.parameters.pop("healthcheck", None)

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
        if step.name == db_models.OrchestratorPlanStep.BUILD:
            step_result = runner.build(step.parameters)
        elif step.name == db_models.OrchestratorPlanStep.RESTART:
            step_result = runner.restart(step.parameters)
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
