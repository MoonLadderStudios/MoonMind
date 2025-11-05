import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Optional
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.config.settings import settings
from moonmind.workflows.speckit_celery.job_container import (
    JobContainerManager,
    _collect_secret_environment,
)
from moonmind.workflows.speckit_celery.tasks import (
    AgentConfigurationSnapshot,
    persist_agent_configuration,
    select_agent_configuration,
)
from moonmind.workflows.speckit_celery.workspace import SpecWorkspaceManager


def test_cleanup_workspace_preserves_artifacts(tmp_path):
    """Workspace cleanup should remove repo/home while retaining artifacts."""

    manager = SpecWorkspaceManager(tmp_path)
    run_id = uuid4()
    paths = manager.ensure_workspace(run_id)

    (paths.repo_path / "README.md").write_text("repo", encoding="utf-8")
    (paths.home_path / ".config").write_text("config", encoding="utf-8")
    stray = paths.run_root / "notes.txt"
    stray.write_text("notes", encoding="utf-8")
    (paths.artifacts_path / "log.txt").write_text("log", encoding="utf-8")

    manager.cleanup_workspace(run_id)

    assert not paths.repo_path.exists()
    assert not paths.home_path.exists()
    assert not stray.exists()
    assert paths.artifacts_path.exists()
    assert (paths.artifacts_path / "log.txt").read_text(encoding="utf-8") == "log"
    assert paths.run_root.exists()


def test_cleanup_workspace_remove_artifacts(tmp_path):
    """When requested, cleanup should remove the entire workspace tree."""

    manager = SpecWorkspaceManager(tmp_path)
    run_id = uuid4()
    paths = manager.ensure_workspace(run_id)
    (paths.artifacts_path / "log.txt").write_text("log", encoding="utf-8")

    manager.cleanup_workspace(run_id, remove_artifacts=True)

    assert not paths.run_root.exists()


def test_cleanup_job_container_invokes_docker(monkeypatch, tmp_path):
    """Container cleanup should invoke Docker SDK removal helpers."""

    manager = SpecWorkspaceManager(tmp_path)
    run_id = uuid4()
    container_name = manager.job_container_name(run_id)

    class DummyNotFound(Exception):
        pass

    class DummyDockerException(Exception):
        pass

    removed = {"count": 0}

    class DummyContainer:
        def remove(self, force: bool = True) -> None:
            removed["count"] += 1

    containers = {container_name: DummyContainer()}

    class DummyContainers:
        def get(self, name: str) -> DummyContainer:
            if name not in containers:
                raise DummyNotFound()
            return containers[name]

    dummy_client = SimpleNamespace(containers=DummyContainers())
    dummy_module = SimpleNamespace(
        from_env=lambda: dummy_client,
        errors=SimpleNamespace(
            DockerException=DummyDockerException, NotFound=DummyNotFound
        ),
    )

    monkeypatch.setitem(sys.modules, "docker", dummy_module)
    monkeypatch.setitem(sys.modules, "docker.errors", dummy_module.errors)

    assert manager.cleanup_job_container(run_id) is True
    assert removed["count"] == 1

    # Subsequent call should detect the missing container and return False
    containers.pop(container_name)
    assert manager.cleanup_job_container(run_id) is False


def test_purge_expired_workspaces_removes_old(monkeypatch, tmp_path):
    """Expired workspaces should be pruned and containers cleaned."""

    manager = SpecWorkspaceManager(tmp_path)
    old_run = uuid4()
    recent_run = uuid4()
    old_paths = manager.ensure_workspace(old_run)
    recent_paths = manager.ensure_workspace(recent_run)

    (old_paths.repo_path / "file.txt").write_text("old", encoding="utf-8")
    (recent_paths.repo_path / "file.txt").write_text("new", encoding="utf-8")

    old_timestamp = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(old_paths.run_root, (old_timestamp, old_timestamp))

    cleaned: list[str] = []

    def _fake_cleanup(run_id: str, *, docker_client=None) -> bool:
        cleaned.append(run_id)
        return True

    monkeypatch.setattr(manager, "cleanup_job_container", _fake_cleanup)

    original_stat = Path.stat

    def _fake_stat(self: Path, *, follow_symlinks: bool = True):
        result = original_stat(self, follow_symlinks=follow_symlinks)
        if self == old_paths.run_root:
            old_time = datetime.now(UTC) - timedelta(days=8)
            timestamp = old_time.timestamp()

            class _StatProxy:
                def __init__(self, base, ts: float) -> None:
                    self._base = base
                    self._timestamp = ts

                def __getattr__(self, name: str):
                    if name in {"st_mtime", "st_ctime"}:
                        return self._timestamp
                    return getattr(self._base, name)

            return _StatProxy(result, timestamp)
        return result

    monkeypatch.setattr(Path, "stat", _fake_stat)

    removed = manager.purge_expired_workspaces(retention=timedelta(days=7))

    assert old_paths.run_root in removed
    assert not old_paths.run_root.exists()
    assert recent_paths.run_root.exists()
    assert str(old_run) in cleaned


def _reset_agent_settings(monkeypatch) -> None:
    """Reset agent-related settings to predictable defaults."""

    monkeypatch.setattr(settings.spec_workflow, "agent_backend", "codex_cli")
    monkeypatch.setattr(
        settings.spec_workflow, "allowed_agent_backends", ("codex_cli",)
    )
    monkeypatch.setattr(settings.spec_workflow, "agent_version", "1.0.0")
    monkeypatch.setattr(settings.spec_workflow, "prompt_pack_version", "2025.11")
    monkeypatch.setattr(
        settings.spec_workflow,
        "agent_runtime_env_keys",
        ("CODEX_API_KEY", "CODEX_ENV"),
    )


def test_select_agent_configuration_uses_settings(monkeypatch):
    """Agent selection should reflect configured defaults and environment."""

    _reset_agent_settings(monkeypatch)
    monkeypatch.setenv("CODEX_API_KEY", "abc123")
    monkeypatch.setenv("CODEX_ENV", "production")

    snapshot = select_agent_configuration()

    assert snapshot.backend == "codex_cli"
    assert snapshot.version == "1.0.0"
    assert snapshot.prompt_pack_version == "2025.11"
    assert snapshot.runtime_env == {
        "CODEX_API_KEY": "abc123",
        "CODEX_ENV": "production",
    }


def test_select_agent_configuration_with_overrides(monkeypatch):
    """Run-specific overrides should take precedence over defaults."""

    _reset_agent_settings(monkeypatch)
    monkeypatch.setattr(
        settings.spec_workflow,
        "allowed_agent_backends",
        ("codex_cli", "stub"),
    )
    monkeypatch.setattr(settings.spec_workflow, "agent_runtime_env_keys", ())

    snapshot = select_agent_configuration(
        {
            "agent_backend": "stub",
            "agent_version": "2.3.4",
            "prompt_pack_version": "pack-9",
            "agent_runtime_env": {"CUSTOM": "value", "CODEX_API_KEY": "override"},
            "agent_runtime_env_keys": ("CUSTOM",),
        }
    )

    assert snapshot.backend == "stub"
    assert snapshot.version == "2.3.4"
    assert snapshot.prompt_pack_version == "pack-9"
    assert snapshot.runtime_env == {
        "CUSTOM": "value",
        "CODEX_API_KEY": "override",
    }


def test_select_agent_configuration_rejects_disallowed(monkeypatch):
    """Selecting a backend outside the allow list should raise an error."""

    _reset_agent_settings(monkeypatch)

    with pytest.raises(ValueError):
        select_agent_configuration({"agent_backend": "forbidden"})


@pytest.mark.asyncio
async def test_persist_agent_configuration_calls_repository(monkeypatch):
    """Persist helper should forward configuration details to the repository."""

    run_id = uuid4()
    snapshot = AgentConfigurationSnapshot(
        backend="codex_cli",
        version="1.0.0",
        prompt_pack_version="2025.11",
        runtime_env={"CODEX_API_KEY": "secret"},
    )

    recorded: dict[str, object] = {}
    sentinel = object()

    class DummyRepo:
        async def upsert_agent_configuration(self, **kwargs):
            recorded.update(kwargs)
            return sentinel

    repo = DummyRepo()

    result = await persist_agent_configuration(repo, run_id=run_id, snapshot=snapshot)

    assert result is sentinel
    assert recorded["run_id"] == run_id
    assert recorded["agent_backend"] == "codex_cli"
    assert recorded["agent_version"] == "1.0.0"
    assert recorded["prompt_pack_version"] == "2025.11"
    assert recorded["runtime_env"] == {"CODEX_API_KEY": "***REDACTED***"}


def test_select_agent_configuration_rejects_mapping_runtime_keys(monkeypatch):
    """Mappings passed as runtime key overrides should raise a clear error."""

    _reset_agent_settings(monkeypatch)

    with pytest.raises(ValueError):
        select_agent_configuration({"agent_runtime_env_keys": {"CODEX": "value"}})


def test_collect_secret_environment_includes_runtime_overrides(monkeypatch):
    """Runtime environment overrides should be included in injected secrets."""

    monkeypatch.setattr(
        settings.spec_workflow, "agent_runtime_env_keys", ("CUSTOM_KEY", "CODEX_ENV")
    )
    monkeypatch.setenv("CUSTOM_KEY", "from-os")
    monkeypatch.setenv("CODEX_ENV", "env-value")
    monkeypatch.setattr(settings.spec_workflow, "codex_environment", "config-value")

    result = _collect_secret_environment({"CUSTOM_KEY": "override", "EXTRA": 42})

    assert result["CUSTOM_KEY"] == "override"
    assert result["EXTRA"] == "42"
    assert result["CODEX_ENV"] == "config-value"


def test_job_container_start_injects_runtime_environment(monkeypatch):
    """Job container start should forward runtime environment variables."""

    injected: dict[str, object] = {}

    def fake_collect(runtime_environment=None):
        injected["runtime"] = runtime_environment
        secrets = {"SECRET": "value"}
        if runtime_environment:
            secrets.update(runtime_environment)
        return secrets

    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.job_container._collect_secret_environment",
        fake_collect,
    )

    class DummyContainers:
        def __init__(self) -> None:
            self.environment: Optional[dict[str, str]] = None

        def run(self, *args, **kwargs):
            self.environment = kwargs.get("environment")
            return SimpleNamespace(id="container-id", name="container-name")

    dummy_client = SimpleNamespace(containers=DummyContainers())
    monkeypatch.setattr(settings.spec_workflow, "job_image", "example:latest")
    monkeypatch.setattr(settings.spec_workflow, "workspace_root", "/tmp/workspace")

    manager = JobContainerManager(docker_client=dummy_client)
    manager.start(
        "run-123",
        environment={"BASE": "1"},
        runtime_environment={"CUSTOM": "value"},
        cleanup_existing=False,
    )

    assert injected["runtime"] == {"CUSTOM": "value"}
    assert dummy_client.containers.environment is not None
    assert dummy_client.containers.environment["BASE"] == "1"
    assert dummy_client.containers.environment["SECRET"] == "value"
    assert dummy_client.containers.environment["CUSTOM"] == "value"
