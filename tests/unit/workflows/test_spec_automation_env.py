import os
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.config.settings import settings
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
                raise DummyNotFound
            return containers[name]

    dummy_client = SimpleNamespace(containers=DummyContainers())
    dummy_module = SimpleNamespace(
        from_env=lambda: dummy_client,
        errors=SimpleNamespace(
            DockerException=DummyDockerException, NotFound=DummyNotFound
        ),
    )

    monkeypatch.setitem(sys.modules, "docker", dummy_module)

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
