import subprocess
from typing import Any

from moonmind.workflows.automation import models
from moonmind.workflows.automation import preflight


def test_docker_sidecar_preflight_probes_info_and_pinned_ue_manifest(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    captured_envs: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        captured_envs.append(kwargs["env"])
        assert kwargs["timeout"] == 15
        if args == ["docker", "info"]:
            return subprocess.CompletedProcess(args, 0, stdout="Server OK\n", stderr="")
        if args == [
            "docker",
            "manifest",
            "inspect",
            "ghcr.io/acme/unreal-engine:5.4",
        ]:
            return subprocess.CompletedProcess(args, 0, stdout='{"schemaVersion":2}', stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.run_docker_sidecar_preflight_check(
        env={
            "DOCKER_HOST": "unix:///var/run/moonmind-docker/docker.sock",
            "MOONMIND_UNREAL_ENGINE_IMAGE": "ghcr.io/acme/unreal-engine:5.4",
            "MOONMIND_NUMERIC_ENV": 5,
            "MOONMIND_NULL_ENV": None,
        },
        timeout=15,
    )

    assert result.status is models.CodexPreflightStatus.PASSED
    assert result.failure_class is None
    assert result.diagnostics_ref == "preflight://docker-sidecar"
    assert calls == [
        ["docker", "info"],
        ["docker", "manifest", "inspect", "ghcr.io/acme/unreal-engine:5.4"],
    ]
    assert captured_envs[0]["MOONMIND_NUMERIC_ENV"] == "5"
    assert "MOONMIND_NULL_ENV" not in captured_envs[0]


def test_docker_sidecar_preflight_fails_as_system_error_when_info_fails(
    monkeypatch,
) -> None:
    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == ["docker", "info"]
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        )

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.run_docker_sidecar_preflight_check(
        env={"MOONMIND_MANAGED_SESSION_DOCKER_MODE": "docker-sidecar"}
    )

    assert result.status is models.CodexPreflightStatus.FAILED
    assert result.failure_class == "system_error"
    assert result.diagnostics_ref == "preflight://docker-sidecar"
    assert "Cannot connect to the Docker daemon" in (result.message or "")


def test_docker_sidecar_preflight_fails_as_system_error_on_os_error(
    monkeypatch,
) -> None:
    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == ["docker", "info"]
        raise PermissionError("docker is not executable")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.run_docker_sidecar_preflight_check(
        env={"MOONMIND_MANAGED_SESSION_DOCKER_MODE": "docker-sidecar"}
    )

    assert result.status is models.CodexPreflightStatus.FAILED
    assert result.failure_class == "system_error"
    assert result.diagnostics_ref == "preflight://docker-sidecar"
    assert "docker is not executable" in (result.message or "")


def test_docker_sidecar_preflight_rejects_unpinned_ue_image(monkeypatch) -> None:
    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == ["docker", "info"]
        return subprocess.CompletedProcess(args, 0, stdout="Server OK\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.run_docker_sidecar_preflight_check(
        env={
            "DOCKER_HOST": "unix:///var/run/moonmind-docker/docker.sock",
            "MOONMIND_UNREAL_ENGINE_IMAGE": "ghcr.io/acme/unreal-engine:latest",
        },
    )

    assert result.status is models.CodexPreflightStatus.FAILED
    assert result.failure_class == "system_error"
    assert "must be pinned" in (result.message or "")
