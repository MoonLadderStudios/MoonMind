"""Docker backend policy and transport boundary tests for MoonMind#3254."""

from __future__ import annotations

import pytest

from moonmind.schemas.container_job_models import ResolvedContainerLaunchPlan
from moonmind.workloads.docker_backend import (
    DockerBackendError,
    DockerBackendPolicy,
    DockerBackendSettings,
    DockerEngineAdapter,
)


def settings(**overrides: str) -> DockerBackendSettings:
    values = {"enabled": True, "default_backend_ref": "system", "kind": "docker-engine", "endpoint": "tcp://proxy:2375"}
    values.update(overrides)
    return DockerBackendSettings(**values)


def plan(**spec_overrides: object) -> ResolvedContainerLaunchPlan:
    spec = {
        "image": "alpine:3.20",
        "workspaceRef": {"kind": "moonmind-session", "sessionId": "s1"},
        "command": ["true"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512, "pids": 64},
    }
    spec.update(spec_overrides)
    return ResolvedContainerLaunchPlan(
        jobId="container-job:" + "a" * 32,
        backendKind="docker-engine",
        backendRef="system",
        resolvedWorkspaceRef="/work/agent_jobs/job-1/repo",
        spec=spec,
    )


def test_settings_fail_closed_for_unsupported_or_missing_endpoint() -> None:
    with pytest.raises(DockerBackendError, match="unsupported"):
        settings(kind="podman").validate()
    with pytest.raises(DockerBackendError, match="SYSTEM_DOCKER_HOST"):
        settings(endpoint="").validate()


def test_create_boundary_applies_security_resources_mounts_and_immutable_labels() -> None:
    adapter = DockerEngineAdapter(
        settings(),
        policy=DockerBackendPolicy(allowed_environment=frozenset({"CI"})),
    )
    args = adapter.build_create_args(plan(environment=[{"name": "CI", "value": "1"}]))
    rendered = " ".join(args)
    assert "--privileged=false" in args
    assert "--cap-drop ALL" in rendered
    assert "--security-opt no-new-privileges" in rendered
    assert "--pid private" in rendered and "--ipc private" in rendered
    assert "--pids-limit 64" in rendered
    assert "source=/work/agent_jobs/job-1/repo,target=/workspace" in rendered
    assert "moonmind.job_id=container-job:" in rendered
    assert "--device" not in args and "/var/run/docker.sock" not in rendered


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"resources": {"cpuMillis": 9000, "memoryMiB": 512, "pids": 64}}, "cpuMillis"),
        ({"resources": {"cpuMillis": 1000, "memoryMiB": 20000, "pids": 64}}, "memoryMiB"),
        ({"resources": {"cpuMillis": 1000, "memoryMiB": 512, "pids": 2000}}, "pids"),
        ({"timeoutSeconds": 20000}, "timeoutSeconds"),
        ({"environment": [{"name": "PATH", "value": "bad"}]}, "allowlisted"),
    ],
)
def test_deployment_ceilings_and_environment_policy_cannot_be_overridden(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(DockerBackendError, match=message):
        DockerEngineAdapter(settings()).build_create_args(plan(**overrides))


def test_forbidden_workspace_sources_are_rejected() -> None:
    candidate = plan()
    candidate.resolved_workspace_ref = "/var/lib/docker/overlay2"
    with pytest.raises(DockerBackendError, match="approved"):
        DockerEngineAdapter(settings()).build_create_args(candidate)


def test_raw_docker_cli_is_disabled_by_default_and_endpoint_is_deployment_only() -> None:
    loaded = DockerBackendSettings.from_environment({"SYSTEM_DOCKER_HOST": "unix:///run/proxy.sock"})
    assert loaded.allow_raw_docker_cli is False
    assert loaded.endpoint == "unix:///run/proxy.sock"
