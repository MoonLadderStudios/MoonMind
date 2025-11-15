"""Unit tests for orchestrator command runner helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.workflows.orchestrator.command_runner import (
    CommandExecutionError,
    CommandRunner,
)
from moonmind.workflows.orchestrator.service_profiles import ServiceProfile
from moonmind.workflows.orchestrator.storage import ArtifactStorage


class _StubResponse(SimpleNamespace):
    """Simple HTTP response stub exposing a ``status_code`` attribute."""


def _make_profile(workspace: Path) -> ServiceProfile:  # pragma: no cover - helper
    return ServiceProfile(
        key="test",
        compose_service="svc",
        workspace_path=workspace,
        allowlist_globs=("**",),
    )


def test_verify_timeout_emits_artifact(tmp_path, monkeypatch):
    """Timeouts should persist the verify log before raising an error."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fake_request(method: str, url: str, timeout: float) -> _StubResponse:
        return _StubResponse(status_code=500)

    monkeypatch.setattr(
        "moonmind.workflows.orchestrator.command_runner.httpx.request", fake_request
    )

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.verify(
            {
                "logArtifact": "verify.log",
                "healthcheck": {
                    "url": "http://example.test",
                    "timeoutSeconds": 0,
                    "intervalSeconds": 0,
                    "expectedStatus": 200,
                },
            }
        )

    error = excinfo.value
    assert error.artifacts, "verify failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "Attempt 1" in contents
    assert "timed out" in contents


def test_build_failure_emits_log_artifact(tmp_path, monkeypatch):
    """Failed builds should persist their log output before raising."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_execute(self, command, *, cwd=None):  # pragma: no cover - test hook
        raise CommandExecutionError("build failed", output="build output")

    monkeypatch.setattr(
        CommandRunner,
        "_execute_command",
        fail_execute,
    )

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    error = excinfo.value
    assert error.artifacts, "build failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    assert log_path.read_text() == "build output"


def test_restart_failure_emits_log_artifact(tmp_path, monkeypatch):
    """Failed restarts should persist their log output before raising."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_execute(self, command, *, cwd=None):  # pragma: no cover - test hook
        raise CommandExecutionError("restart failed", output="restart output")

    monkeypatch.setattr(
        CommandRunner,
        "_execute_command",
        fail_execute,
    )

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.restart({"logArtifact": "restart.log"})

    error = excinfo.value
    assert error.artifacts, "restart failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    assert log_path.read_text() == "restart output"


def test_build_failure_without_output_uses_command(tmp_path, monkeypatch):
    """Empty subprocess output should fall back to the formatted command."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_execute(self, command, *, cwd=None):  # pragma: no cover - test hook
        del command, cwd
        raise CommandExecutionError("build failed")

    monkeypatch.setattr(CommandRunner, "_execute_command", fail_execute)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"command": ["docker", "compose", "build"], "logArtifact": "build.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "docker compose build" in contents
