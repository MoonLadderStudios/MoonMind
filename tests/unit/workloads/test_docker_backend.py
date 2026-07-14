"""Docker backend policy and transport boundary tests for MoonMind#3254."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        correlationId="workflow-1/run-1",
        expiresAt=datetime.now(UTC) + timedelta(hours=1),
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
    assert "--userns private" in rendered
    assert "--pids-limit 64" in rendered
    assert "source=/work/agent_jobs/job-1/repo,target=/workspace" in rendered
    assert "moonmind.job_id=container-job:" in rendered
    assert "moonmind.correlation_id=workflow-1/run-1" in rendered
    assert "moonmind.expires_at=" in rendered
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


def test_only_resolver_approved_mount_classes_and_roots_are_accepted() -> None:
    approved_data = plan().model_dump(mode="json", by_alias=True)
    approved_data["resolvedMounts"] = [
        {"mountClass": "workspace", "resolvedRef": "/work/agent_jobs/job-1/repo", "target": "/workspace"},
        {"mountClass": "scratch", "resolvedRef": "/work/agent_jobs/job-1/scratch", "target": "/scratch"},
        {"mountClass": "artifact", "resolvedRef": "/var/artifacts/job-1", "target": "/artifacts"},
    ]
    approved = ResolvedContainerLaunchPlan.model_validate(approved_data)
    rendered = " ".join(DockerEngineAdapter(settings()).build_create_args(approved))
    assert "source=/var/artifacts/job-1,target=/artifacts" in rendered
    rejected_data = plan().model_dump(mode="json", by_alias=True)
    rejected_data["resolvedMounts"] = [{"mountClass": "workspace", "resolvedRef": "/etc", "target": "/workspace"}]
    rejected = ResolvedContainerLaunchPlan.model_validate(rejected_data)
    with pytest.raises(DockerBackendError, match="approved"):
        DockerEngineAdapter(settings()).build_create_args(rejected)


def test_raw_docker_cli_is_disabled_by_default_and_endpoint_is_deployment_only() -> None:
    loaded = DockerBackendSettings.from_environment({"SYSTEM_DOCKER_HOST": "unix:///run/proxy.sock"})
    assert loaded.allow_raw_docker_cli is False
    assert loaded.endpoint == "unix:///run/proxy.sock"


@pytest.mark.parametrize("name", ["MOONMIND_DOCKER_BACKEND_ENABLED", "MOONMIND_ALLOW_RAW_DOCKER_CLI"])
def test_boolean_settings_reject_ambiguous_values(name: str) -> None:
    with pytest.raises(DockerBackendError, match=name):
        DockerBackendSettings.from_environment({"SYSTEM_DOCKER_HOST": "unix:///proxy.sock", name: "enabled-ish"})


@pytest.mark.asyncio
async def test_readiness_error_redacts_endpoint(monkeypatch) -> None:
    adapter = DockerEngineAdapter(settings(endpoint="tcp://secret-host:2375"))

    async def failed(*args, **kwargs):
        return b"", b"cannot reach tcp://secret-host:2375", 1

    monkeypatch.setattr(adapter, "_command", failed)
    with pytest.raises(DockerBackendError, match="<docker-endpoint>") as error:
        await adapter.ready()
    assert "secret-host" not in str(error.value)


@pytest.mark.asyncio
async def test_secret_values_are_never_put_in_create_argv(monkeypatch) -> None:
    adapter = DockerEngineAdapter(settings(), policy=DockerBackendPolicy(allowed_environment=frozenset({"API_TOKEN"})))
    candidate = plan(environment=[{"name": "API_TOKEN", "secretRef": "secret://token"}])
    calls = []

    async def command(args, **kwargs):
        calls.append(tuple(args))
        if args[0] == "ps":
            return b"", b"", 0
        if args[0] == "create":
            return b"cid\n", b"", 0
        if args[0] == "inspect":
            return f"moonmind|container-job|{candidate.job_id}|system|/{adapter._name(candidate.job_id)}\n".encode(), b"", 0
        if args[0] == "wait":
            return b"0\n", b"", 0
        return b"", b"", 0

    monkeypatch.setattr(adapter, "_command", command)
    await adapter.run(candidate, secrets={"secret://token": "super-secret"})
    assert "super-secret" not in repr(calls)
    create = next(call for call in calls if call[0] == "create")
    assert "--env-file" in create
