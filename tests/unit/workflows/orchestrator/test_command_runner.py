"""Unit tests for orchestrator command runner helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.workflows.orchestrator.command_runner import (
    CommandExecutionError,
    CommandRunner,
    CommandRunnerError,
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

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="build output",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    error = excinfo.value
    assert error.artifacts, "build failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "build output" in contents
    assert "docker compose" in contents
    assert artifact.path in str(error)
    assert error.metadata and error.metadata["log"].endswith("build.log")


def test_restart_failure_emits_log_artifact(tmp_path, monkeypatch):
    """Failed restarts should persist their log output before raising."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="restart output",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.restart({"logArtifact": "restart.log"})

    error = excinfo.value
    assert error.artifacts, "restart failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "restart output" in contents
    assert "docker compose" in contents
    assert artifact.path in str(error)
    assert error.metadata and error.metadata["log"].endswith("restart.log")


def test_build_failure_without_artifact_creates_fallback_log(tmp_path, monkeypatch):
    """If logging fails early, the build step should still persist a log."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    # pragma: no cover
    def fail_logged_command(self, *, command, workspace, log_name):
        del command, workspace, log_name
        raise CommandExecutionError("build failed")

    monkeypatch.setattr(CommandRunner, "_run_logged_command", fail_logged_command)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "build failed" in contents
    error = excinfo.value
    assert error.metadata and error.metadata["log"].endswith("build.log")


def test_restart_failure_without_artifact_creates_fallback_log(tmp_path, monkeypatch):
    """Restart failures should create fallback logs if logging aborts early."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    # pragma: no cover
    def fail_logged_command(self, *, command, workspace, log_name):
        del command, workspace, log_name
        raise CommandExecutionError("restart failed")

    monkeypatch.setattr(CommandRunner, "_run_logged_command", fail_logged_command)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.restart({"logArtifact": "restart.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "restart failed" in contents
    error = excinfo.value
    assert error.metadata and error.metadata["log"].endswith("restart.log")


def test_build_failure_reuses_existing_artifact(tmp_path, monkeypatch):
    """Re-run failures should not duplicate existing log artifacts."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    artifact = storage.write_text(run_id, "build.log", "existing log")
    error = CommandExecutionError("build failed")
    error.artifacts = [artifact]

    # pragma: no cover
    def fail_logged_command(self, *, command, workspace, log_name):
        del command, workspace, log_name
        raise error

    monkeypatch.setattr(CommandRunner, "_run_logged_command", fail_logged_command)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    assert len(error.artifacts) == 1
    artifact_path = storage.ensure_run_directory(run_id) / artifact.path
    assert artifact_path.read_text() == "existing log"
    metadata = excinfo.value.metadata
    assert metadata and metadata["log"].endswith("build.log")


def test_build_failure_without_output_uses_command(tmp_path, monkeypatch):
    """Empty subprocess output should fall back to the formatted command."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build(
            {"command": ["docker", "compose", "build"], "logArtifact": "build.log"}
        )

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "docker compose build" in contents
    error = excinfo.value
    assert error.metadata and error.metadata["log"].endswith("build.log")


def test_restart_failure_without_output_uses_command(tmp_path, monkeypatch):
    """Restart failures without output should store the executed command."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.restart(
            {
                "command": [
                    "docker",
                    "compose",
                    "up",
                    "--no-deps",
                    profile.compose_service,
                ],
                "logArtifact": "restart.log",
            }
        )

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "docker compose up --no-deps" in contents
    error = excinfo.value
    assert error.metadata and error.metadata["log"].endswith("restart.log")


def test_build_failure_from_subprocess_persists_combined_output(tmp_path, monkeypatch):
    """Docker build failures should emit stdout and stderr before raising."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - helper
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="compose stdout",
            stderr="compose stderr",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    contents = log_path.read_text()
    assert "compose stdout" in contents
    assert "compose stderr" in contents


def test_restart_failure_from_subprocess_persists_combined_output(
    tmp_path, monkeypatch
):
    """Docker restart failures should emit stdout and stderr before raising."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - helper
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="restart stdout",
            stderr="restart stderr",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.restart({"logArtifact": "restart.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    contents = log_path.read_text()
    assert "restart stdout" in contents
    assert "restart stderr" in contents


def test_patch_command_failure_persists_log(tmp_path, monkeypatch):
    """Patch command failures should record logs for troubleshooting."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_execute(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        if command[0] == "apply":
            raise CommandExecutionError("patch failed", output="patch output")
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(CommandRunner, "_execute_command", fail_execute)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner.patch({"commands": [["apply", "fix"]], "logArtifact": "patch.log"})

    error = excinfo.value
    assert error.artifacts, "patch failure should include log artifacts"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "apply fix" in contents
    assert "patch output" in contents
    assert error.metadata and error.metadata["log"].endswith("patch.log")


def test_run_logged_command_persists_artifact_on_failure(tmp_path, monkeypatch):
    """The helper should attach log artifacts before re-raising errors."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=1,
            stdout="failure output",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CommandExecutionError) as excinfo:
        runner._run_logged_command(
            command=["docker", "compose", "build"],
            workspace=tmp_path,
            log_name="custom.log",
        )

    error = excinfo.value
    assert error.artifacts, "failure should include the log artifact"
    artifact = error.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    contents = log_path.read_text()
    assert "failure output" in contents
    assert artifact.path in str(error)
    assert error.metadata and error.metadata["log"].endswith("custom.log")


def test_build_failure_handles_generic_runner_error(tmp_path, monkeypatch):
    """Errors derived from ``CommandRunnerError`` should record logs."""

    class CustomRunnerError(CommandRunnerError):
        pass

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def fail_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        raise CustomRunnerError("custom failure")

    monkeypatch.setattr(CommandRunner, "_invoke_command", fail_invoke)

    with pytest.raises(CustomRunnerError) as excinfo:
        runner.build({"logArtifact": "build.log"})

    artifact = excinfo.value.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
    assert "custom failure" in log_path.read_text()
    error = excinfo.value
    assert error.metadata and error.metadata["log"].endswith("build.log")


def test_build_step_metadata_includes_log_path(tmp_path, monkeypatch):
    """Successful builds should expose the log artifact path in metadata."""

    profile = _make_profile(tmp_path)
    storage = ArtifactStorage(tmp_path)
    run_id = uuid4()
    runner = CommandRunner(run_id=run_id, profile=profile, artifact_storage=storage)

    def succeed_invoke(self, command, *, cwd=None):  # pragma: no cover - test hook
        del cwd
        cmd_sequence = list(command)
        completed = subprocess.CompletedProcess(
            args=cmd_sequence,
            returncode=0,
            stdout="build ok",
            stderr="",
        )
        return cmd_sequence, completed

    monkeypatch.setattr(CommandRunner, "_invoke_command", succeed_invoke)

    result = runner.build({"logArtifact": "build.log"})

    assert result.metadata and result.metadata["log"].endswith("build.log")
    artifact = result.artifacts[0]
    log_path = storage.ensure_run_directory(run_id) / artifact.path
    assert log_path.exists()
