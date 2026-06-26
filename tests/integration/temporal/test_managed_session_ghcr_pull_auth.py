from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.managed_session_models import (
    LaunchCodexManagedSessionRequest,
    ManagedGitHubCredentialDescriptor,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _request(workspace_root: Path) -> LaunchCodexManagedSessionRequest:
    return LaunchCodexManagedSessionRequest(
        agentRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:1.0",
        environment={
            "MOONMIND_MANAGED_SESSION_DOCKER_MODE": "docker-sidecar",
            "MOONMIND_WORKFLOW_DOCKER_MODE": "profiles",
        },
        dockerCapability={
            "allowed": True,
            "activation": "on_launch",
            "manifestImageRef": (
                "ghcr.io/moonladderstudios/moonmind-unreal-runner"
                "@sha256:"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            "timeoutSeconds": 0,
            "intervalSeconds": 0,
        },
    )


async def _launch_with_fake_docker(
    request: LaunchCodexManagedSessionRequest,
    *,
    tmp_path: Path,
) -> tuple[
    DockerCodexManagedSessionController,
    ManagedSessionStore,
    list[tuple[tuple[str, ...], dict[str, str] | None]],
]:
    commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> tuple[int, str, str]:
        commands.append((command, env))
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:3] == ("docker", "volume", "create"):
            return 0, command[3] + "\n", ""
        if command[:3] == ("docker", "manifest", "inspect"):
            return 0, '{"schemaVersion": 2}\n', ""
        if command[:2] == ("docker", "run"):
            name = command[command.index("--name") + 1]
            if name.endswith("-docker"):
                return 0, "sidecar-ctr\n", ""
            if name.endswith("-agent"):
                return 0, "agent-ctr\n", ""
        if command[:3] == ("docker", "exec", "-e") and "docker" in command:
            if "version" in command:
                return 0, "27.0.0\n", ""
            return 0, '"27.0.0"\n', ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": request.session_id,
                    "sessionEpoch": 1,
                    "containerId": "agent-ctr",
                    "threadId": request.thread_id,
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://moonmind-session-sess-1-agent",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    store = ManagedSessionStore(tmp_path / "session-store")
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(Path(request.workspace_path).parents[1]),
        session_store=store,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )
    await controller.launch_session(request)
    return controller, store, commands


@pytest.mark.asyncio
async def test_sidecar_bootstrap_writes_session_docker_config_when_ghcr_secret_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root)

    async def _fake_resolver(
        _environment: dict[str, str],
        **_kwargs: Any,
    ) -> tuple[str, str]:
        return "pull-user", "pull-token"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller."
        "resolve_ghcr_pull_credentials_for_launch",
        _fake_resolver,
    )

    _controller, store, commands = await _launch_with_fake_docker(
        request,
        tmp_path=tmp_path,
    )

    config_path = (
        Path(request.session_workspace_path) / ".docker" / "config.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["auths"]["ghcr.io"]["auth"] == base64.b64encode(
        b"pull-user:pull-token"
    ).decode("ascii")
    if os.name == "posix" and os.geteuid() == 0:
        assert (config_path.stat().st_uid, config_path.stat().st_gid) == (1000, 1000)

    manifest_call = next(
        item for item in commands if item[0][:3] == ("docker", "manifest", "inspect")
    )
    assert manifest_call[1] is not None
    assert manifest_call[1]["DOCKER_CONFIG"] == str(config_path.parent)

    agent_run = next(
        command
        for command, _env in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    assert f"DOCKER_CONFIG={request.session_workspace_path}/.docker" in agent_run

    record = store.load(request.session_id)
    assert record is not None
    docker_pull = record.metadata["capabilities"]["dockerPull"]
    assert docker_pull["pullAuth"] == "authenticated"
    assert docker_pull["manifestProbe"]["status"] == "passed"
    assert docker_pull["manifestProbe"]["pullAuth"] == "authenticated"
    assert "pull-token" not in json.dumps(record.metadata)


@pytest.mark.asyncio
async def test_sidecar_bootstrap_uses_anonymous_pull_auth_when_ghcr_secret_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root)

    async def _fake_resolver(_environment: dict[str, str], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller."
        "resolve_ghcr_pull_credentials_for_launch",
        _fake_resolver,
    )

    _controller, store, commands = await _launch_with_fake_docker(
        request,
        tmp_path=tmp_path,
    )

    config_path = Path(request.session_workspace_path) / ".docker" / "config.json"
    assert not config_path.exists()

    manifest_call = next(
        item for item in commands if item[0][:3] == ("docker", "manifest", "inspect")
    )
    assert manifest_call[1] == {}

    agent_run = next(
        command
        for command, _env in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    assert "DOCKER_CONFIG=" not in " ".join(agent_run)

    record = store.load(request.session_id)
    assert record is not None
    docker_pull = record.metadata["capabilities"]["dockerPull"]
    assert docker_pull["pullAuth"] == "anonymous"
    assert docker_pull["manifestProbe"]["status"] == "passed"
    assert docker_pull["manifestProbe"]["pullAuth"] == "anonymous"


@pytest.mark.asyncio
async def test_sidecar_bootstrap_uses_github_token_for_ghcr_when_pull_secret_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root).model_copy(
        update={
            "environment": {
                **_request(workspace_root).environment,
                "GITHUB_TOKEN": "github-token",
            }
        }
    )

    async def _fake_github_login(_token: str) -> str:
        return "github-user"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "_resolve_github_login_for_token",
        _fake_github_login,
    )

    _controller, store, commands = await _launch_with_fake_docker(
        request,
        tmp_path=tmp_path,
    )

    config_path = Path(request.session_workspace_path) / ".docker" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["auths"]["ghcr.io"]["auth"] == base64.b64encode(
        b"github-user:github-token"
    ).decode("ascii")

    manifest_call = next(
        item for item in commands if item[0][:3] == ("docker", "manifest", "inspect")
    )
    assert manifest_call[1] == {"DOCKER_CONFIG": str(config_path.parent)}

    agent_run = next(
        command
        for command, _env in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    rendered_agent_run = " ".join(agent_run)
    assert f"DOCKER_CONFIG={request.session_workspace_path}/.docker" in agent_run
    assert "GITHUB_TOKEN=github-token" not in rendered_agent_run
    assert "github-token" not in rendered_agent_run

    record = store.load(request.session_id)
    assert record is not None
    docker_pull = record.metadata["capabilities"]["dockerPull"]
    assert docker_pull["pullAuth"] == "authenticated"
    assert docker_pull["manifestProbe"]["pullAuth"] == "authenticated"
    assert "github-token" not in json.dumps(record.metadata)


@pytest.mark.asyncio
async def test_sidecar_bootstrap_uses_github_credential_descriptor_for_ghcr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root).model_copy(
        update={
            "github_credential": ManagedGitHubCredentialDescriptor(
                source="environment",
                envVar="WORKFLOW_GITHUB_TOKEN",
            )
        }
    )
    monkeypatch.setenv("WORKFLOW_GITHUB_TOKEN", "workflow-github-token")

    async def _fake_github_login(_token: str) -> str:
        return "github-user"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "_resolve_github_login_for_token",
        _fake_github_login,
    )

    _controller, store, commands = await _launch_with_fake_docker(
        request,
        tmp_path=tmp_path,
    )

    config_path = Path(request.session_workspace_path) / ".docker" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["auths"]["ghcr.io"]["auth"] == base64.b64encode(
        b"github-user:workflow-github-token"
    ).decode("ascii")

    agent_run = next(
        command
        for command, _env in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    rendered_agent_run = " ".join(agent_run)
    assert f"DOCKER_CONFIG={request.session_workspace_path}/.docker" in agent_run
    assert "workflow-github-token" not in rendered_agent_run

    record = store.load(request.session_id)
    assert record is not None
    docker_pull = record.metadata["capabilities"]["dockerPull"]
    assert docker_pull["pullAuth"] == "authenticated"
    assert "workflow-github-token" not in json.dumps(record.metadata)


@pytest.mark.asyncio
async def test_sidecar_bootstrap_scrubs_ghcr_env_credentials_from_agent_container(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root).model_copy(
        update={
            "environment": {
                **_request(workspace_root).environment,
                "GHCR_PULL_USER": "pull-user",
                "GHCR_PULL_TOKEN": "pull-token",
            }
        }
    )

    _controller, store, commands = await _launch_with_fake_docker(
        request,
        tmp_path=tmp_path,
    )

    config_path = Path(request.session_workspace_path) / ".docker" / "config.json"
    assert config_path.exists()

    manifest_call = next(
        item for item in commands if item[0][:3] == ("docker", "manifest", "inspect")
    )
    assert manifest_call[1] == {"DOCKER_CONFIG": str(config_path.parent)}

    agent_run = next(
        command
        for command, _env in commands
        if command[:2] == ("docker", "run")
        and "moonmind-session-sess-1-agent" in command
    )
    rendered_agent_run = " ".join(agent_run)
    assert f"DOCKER_CONFIG={request.session_workspace_path}/.docker" in agent_run
    assert "GHCR_PULL_USER=" not in rendered_agent_run
    assert "GHCR_PULL_TOKEN=" not in rendered_agent_run
    assert "pull-token" not in rendered_agent_run

    record = store.load(request.session_id)
    assert record is not None
    assert "pull-token" not in json.dumps(record.metadata)


@pytest.mark.asyncio
async def test_sidecar_bootstrap_cleans_docker_config_when_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    request = _request(workspace_root)
    commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    async def _fake_resolver(
        _environment: dict[str, str],
        **_kwargs: Any,
    ) -> tuple[str, str]:
        return "pull-user", "pull-token"

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> tuple[int, str, str]:
        del input_text
        commands.append((command, env))
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:4] == ("docker", "volume", "rm", "-f"):
            return 0, "", ""
        if command[:3] == ("docker", "manifest", "inspect"):
            return 1, "", "denied"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_session_controller."
        "resolve_ghcr_pull_credentials_for_launch",
        _fake_resolver,
    )

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(Path(request.workspace_path).parents[1]),
        session_store=ManagedSessionStore(tmp_path / "session-store"),
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="preflight manifest probe failed"):
        await controller.launch_session(request)

    config_path = Path(request.session_workspace_path) / ".docker" / "config.json"
    assert not config_path.exists()
    assert any(
        command[:3] == ("docker", "manifest", "inspect") for command, _env in commands
    )
