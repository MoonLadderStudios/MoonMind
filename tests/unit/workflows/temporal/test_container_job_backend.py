"""Adapter policy/hardening coverage for MoonLadderStudios/MoonMind#3254."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    EGRESS_CONFIG_DIGEST,
    EGRESS_NETWORK_REF,
    EGRESS_PROFILE_SET_DIGEST,
    ENFORCER_IMPLEMENTATION,
    OMNIGENT_EGRESS_NETWORK_REF,
    PROXY_URL,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendReadinessError,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.workflows.temporal.container_job_backend import (
    LABEL_BACKEND_REF,
    LABEL_CORRELATION,
    LABEL_EXPIRES_AT,
    LABEL_OBJECT_KIND,
    LABEL_OWNERSHIP,
    LABEL_OWNERSHIP_SCHEMA,
    OWNERSHIP_SCHEMA_VERSION,
    ContainerJobBackend,
    DockerContainerJobBackend,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"


def _request(tmp_path, **spec_overrides) -> ContainerJobActivityRequest:
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": ["python", "-V"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(spec_overrides)
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": f"{JOB_ID}:v1",
        "request": {
            "idempotencyKey": "issue-3254",
            "source": {"source": "workflow", "workflowId": "mm:3254"},
            "spec": spec,
        },
        "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
        "resolvedImageRef": "sha256:" + "a" * 64,
    }
    return ContainerJobActivityRequest.model_validate(payload)


def _recording_runner(commands, *, ownership: bytes | None = None, missing=True):
    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            if ownership is None:
                return (1, b"", b"no such container") if missing else (0, b"", b"")
            return 0, b'{"moonmind.ownership":"' + ownership + b'"}', b""
        if args[0] == "version":
            return 0, b"27.0.0", b""
        return 0, b"", b""

    return runner


def test_production_backend_satisfies_protocol() -> None:
    assert isinstance(
        DockerContainerJobBackend(workspace_root="/tmp"), ContainerJobBackend
    )


@pytest.mark.asyncio
async def test_readiness_fails_when_disabled_or_unreachable(tmp_path) -> None:
    disabled = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_ENABLED": "false"}
    )
    backend = DockerContainerJobBackend(workspace_root=tmp_path, settings=disabled)
    with pytest.raises(ContainerBackendReadinessError):
        await backend.check_readiness()

    async def failing_runner(args):
        return 1, b"", b"cannot connect to the Docker daemon"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=failing_runner
    )
    with pytest.raises(ContainerBackendReadinessError):
        await backend.check_readiness()


@pytest.mark.asyncio
async def test_readiness_passes_when_endpoint_reachable(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    await backend.check_readiness()
    assert ("version", "--format", "{{.Server.Version}}") in commands


@pytest.mark.asyncio
async def test_readiness_requires_volume_subpath_capable_daemon(tmp_path) -> None:
    async def old_daemon(args):
        assert args[0] == "version"
        return 0, b"25.0.5", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name="agent_workspaces",
        command_runner=old_daemon,
    )

    with pytest.raises(ContainerBackendReadinessError, match="26 or newer"):
        await backend.check_readiness()


@pytest.mark.asyncio
async def test_readiness_accepts_volume_subpath_capable_daemon(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name="agent_workspaces",
        command_runner=_recording_runner(commands),
    )

    await backend.check_readiness()


@pytest.mark.asyncio
async def test_create_applies_hardening_shm_pids_and_labels(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    await backend.create_container(_request(tmp_path))
    create = next(c for c in commands if c[0] == "create")
    # Reused hardening flags from DockerWorkloadLauncher.
    assert "--privileged=false" in create
    assert "--cap-drop" in create and "ALL" in create
    assert "no-new-privileges" in create
    assert "--shm-size" in create
    assert "--pids-limit" in create
    # Immutable ownership/correlation/expiry labels.
    joined = " ".join(create)
    assert f"{LABEL_OWNERSHIP}={JOB_ID}:v1" in joined
    assert f"{LABEL_CORRELATION}=mm:3254" in joined
    assert f"{LABEL_EXPIRES_AT}=" in joined
    assert f"{LABEL_OBJECT_KIND}=container" in joined
    assert f"{LABEL_BACKEND_REF}=system" in joined
    assert f"{LABEL_OWNERSHIP_SCHEMA}={OWNERSHIP_SCHEMA_VERSION}" in joined


@pytest.mark.asyncio
async def test_bridge_launch_requires_attestation_and_uses_restricted_network(
    tmp_path,
) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("network", "inspect"):
            return 0, b'{"Internal":true,"EnableIPv6":false}', b""
        if args[0] == "inspect" and "NetworkSettings.Networks" in args[2]:
            payload = {
                "labels": {
                    "moonmind.egress.profile-set-digest": EGRESS_PROFILE_SET_DIGEST,
                    "moonmind.egress.enforcer": ENFORCER_IMPLEMENTATION,
                    "moonmind.egress.config-digest": EGRESS_CONFIG_DIGEST,
                },
                "networks": {
                    EGRESS_NETWORK_REF: {},
                    "moonmind_sandbox-egress-network": {},
                    OMNIGENT_EGRESS_NETWORK_REF: {},
                    "local-network": {},
                },
                "image": "sha256:gateway-image",
                "health": "healthy",
            }
            return 0, json.dumps(payload).encode(), b""
        if args[:2] == ("exec", DEFAULT_EGRESS_PROFILE.gateway_ref):
            return 0, (
                EGRESS_CONFIG_DIGEST.removeprefix("sha256:")
                + "  /etc/squid/squid.conf\n"
            ).encode(), b""
        return await _recording_runner(commands)(args)

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    await backend.create_container(_request(tmp_path, networkMode="bridge"))

    create = next(command for command in commands if command[0] == "create")
    assert create[create.index("--network") + 1] == EGRESS_NETWORK_REF
    assert f"HTTPS_PROXY={PROXY_URL}" in create
    assert "NO_PROXY=" in create
    assert f"moonmind.egress.profile={DEFAULT_EGRESS_PROFILE.ref}" in create


@pytest.mark.asyncio
async def test_bridge_launch_fails_before_create_when_network_is_not_internal(
    tmp_path,
) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        commands.append(tuple(args))
        if args[:2] == ("network", "inspect"):
            return 0, b'{"Internal":false,"EnableIPv6":false}', b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    with pytest.raises(RuntimeError, match="not internal"):
        await backend.create_container(_request(tmp_path, networkMode="bridge"))
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_create_mounts_authorized_workspace_volume_subpath(tmp_path) -> None:
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name="agent_workspaces",
        command_runner=_recording_runner(commands),
    )
    request = _request(
        tmp_path,
        workspaceRef={"kind": "sandbox", "workspaceId": "run-1"},
    )
    resolved = await backend.resolve_workspace(request)
    request.resolved_workspace_ref = resolved.resolved_workspace_ref
    request.resolved_workspace_volume_name = resolved.resolved_workspace_volume_name
    request.resolved_workspace_volume_subpath = (
        resolved.resolved_workspace_volume_subpath
    )

    await backend.create_container(request)

    create = next(command for command in commands if command[0] == "create")
    mount = create[create.index("--mount") + 1]
    assert mount == (
        "type=volume,src=agent_workspaces,dst=/workspace,"
        "volume-subpath=temporal_sandbox/run-1/repo"
    )
    assert str(workspace) not in mount


@pytest.mark.asyncio
async def test_create_reconstructs_volume_mount_for_pre_volume_activity_shape(
    tmp_path,
) -> None:
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name="agent_workspaces",
        command_runner=_recording_runner(commands),
    )
    request = _request(
        tmp_path,
        workspaceRef={"kind": "sandbox", "workspaceId": "run-1"},
    )
    request.resolved_workspace_ref = str(workspace)

    await backend.create_container(request)

    create = next(command for command in commands if command[0] == "create")
    mount = create[create.index("--mount") + 1]
    assert mount == (
        "type=volume,src=agent_workspaces,dst=/workspace,"
        "volume-subpath=temporal_sandbox/run-1/repo"
    )
    assert str(workspace) not in mount


def test_workspace_volume_name_is_validated(tmp_path) -> None:
    with pytest.raises(ValueError, match="workspace_volume_name"):
        DockerContainerJobBackend(
            workspace_root=tmp_path,
            workspace_volume_name="bad,volume",
        )


@pytest.mark.asyncio
async def test_create_rejects_untrusted_workspace_volume_metadata(tmp_path) -> None:
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        workspace_volume_name="agent_workspaces",
        command_runner=_recording_runner([]),
    )
    request = _request(tmp_path)
    request.resolved_workspace_ref = str(workspace)
    request.resolved_workspace_volume_name = "other_volume"
    request.resolved_workspace_volume_subpath = "temporal_sandbox/run-1/repo"

    with pytest.raises(RuntimeError, match="not deployment-authorized"):
        await backend.create_container(request)


@pytest.mark.asyncio
async def test_remove_is_idempotent_when_owned_container_is_absent(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(commands, missing=True),
    )

    await backend.remove_container(_request(tmp_path))

    assert not any(command[0] == "rm" for command in commands)


@pytest.mark.asyncio
async def test_remove_refuses_replacement_with_mismatched_ownership(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(
            commands, ownership=b"container-job:ffffffffffffffffffffffffffffffff:v1"
        ),
    )

    with pytest.raises(RuntimeError, match="ownership mismatch"):
        await backend.remove_container(_request(tmp_path))

    assert not any(command[0] == "rm" for command in commands)


@pytest.mark.asyncio
async def test_stop_refuses_replacement_with_mismatched_ownership(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(
            commands, ownership=b"container-job:ffffffffffffffffffffffffffffffff:v1"
        ),
    )

    with pytest.raises(RuntimeError, match="ownership mismatch"):
        await backend.stop_container(_request(tmp_path))

    assert not any(command[0] == "stop" for command in commands)


@pytest.mark.asyncio
async def test_create_rejects_resources_above_deployment_ceiling(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    tiny = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_CPU_MILLIS": "500"}
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=tiny,
        command_runner=_recording_runner([]),
    )
    with pytest.raises(RuntimeError, match="ceiling"):
        await backend.create_container(_request(tmp_path))


@pytest.mark.asyncio
async def test_create_mounts_deployment_authorized_cache_refs(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    request = _request(
        tmp_path,
        caches=[
            {
                "cacheRef": "unreal-ccache",
                "target": "/home/ue4/.ccache",
                "readOnly": False,
            },
            {
                "cacheRef": "unreal-ubt",
                "target": "/home/ue4/.config/Epic/UnrealBuildTool",
                "readOnly": False,
            },
        ],
    )
    result = await backend.create_container(request)

    create = next(command for command in commands if command[0] == "create")
    cache_mounts = [
        item for item in create if item.startswith("type=volume,src=unreal_")
    ]
    assert len(cache_mounts) == 2
    assert any(
        item.startswith("type=volume,src=unreal_ccache_volume-")
        and item.endswith(",dst=/home/ue4/.ccache")
        for item in cache_mounts
    )
    assert any(
        item.startswith("type=volume,src=unreal_ubt_volume-")
        and item.endswith("dst=/home/ue4/.config/Epic/UnrealBuildTool")
        for item in cache_mounts
    )
    cache_volume_commands = [
        command for command in commands if command[:2] == ("volume", "create")
    ]
    assert len(cache_volume_commands) == 2
    assert all(
        "moonmind.kind=container-job-cache" in command
        for command in cache_volume_commands
    )
    assert all(
        any(item.startswith("moonmind.cache_owner=") for item in command)
        for command in cache_volume_commands
    )
    assert result.resolved_cache_refs == ("unreal-ccache", "unreal-ubt")


@pytest.mark.asyncio
async def test_cache_volume_is_scoped_to_the_authorized_owner(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    first_commands: list[tuple[str, ...]] = []
    second_commands: list[tuple[str, ...]] = []
    cache = [
        {
            "cacheRef": "unreal-ccache",
            "target": "/home/ue4/.ccache",
            "readOnly": False,
        }
    ]
    first_request = _request(tmp_path, caches=cache)
    second_request = first_request.model_copy(
        update={
            "owner": first_request.owner.model_copy(
                update={"principal_id": "different-owner"}
            )
        }
    )

    await DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(first_commands),
    ).create_container(first_request)
    await DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(second_commands),
    ).create_container(second_request)

    first_mount = next(
        item
        for command in first_commands
        for item in command
        if item.startswith("type=volume,src=unreal_ccache_volume-")
    )
    second_mount = next(
        item
        for command in second_commands
        for item in command
        if item.startswith("type=volume,src=unreal_ccache_volume-")
    )
    assert first_mount != second_mount


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cache,match",
    [
        (
            {"cacheRef": "unknown", "target": "/cache", "readOnly": False},
            "not deployment-authorized",
        ),
        (
            {
                "cacheRef": "unreal-ccache",
                "target": "/different",
                "readOnly": False,
            },
            "requires target",
        ),
        (
            {
                "cacheRef": "unreal-ccache",
                "target": "/home/ue4/.ccache",
                "readOnly": True,
            },
            "requires read-write",
        ),
    ],
)
async def test_create_rejects_cache_authority_mismatch(
    tmp_path, cache: dict[str, object], match: str
) -> None:
    (tmp_path / "art_workspace").mkdir()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner([])
    )
    with pytest.raises(RuntimeError, match=match):
        await backend.create_container(_request(tmp_path, caches=[cache]))


@pytest.mark.asyncio
async def test_create_rejects_ownership_collision(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner([], ownership=b"container-job:deadbeef:v1"),
    )
    with pytest.raises(RuntimeError, match="collision"):
        await backend.create_container(_request(tmp_path))


@pytest.mark.asyncio
async def test_reconcile_reattaches_only_matching_ownership(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 0, json.dumps({LABEL_OWNERSHIP: f"{JOB_ID}:v1"}).encode(), b""
        if args[:2] == ("inspect", "--format"):
            return 0, b"true", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(workspace_root=tmp_path, command_runner=runner)
    result = await backend.reconcile_container(_request(tmp_path))
    assert result.container_ref is not None
    assert result.running is True


@pytest.mark.asyncio
async def test_secret_ref_requires_job_authority(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []
    resolver = AsyncMock(return_value="s3cr3t-value")
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_recording_runner(commands),
        secret_resolver=resolver,
    )
    request = _request(
        tmp_path,
        environment=[{"name": "API_TOKEN", "secretRef": "db://api-token"}],
    )
    with pytest.raises(RuntimeError, match="secretRef is unsupported"):
        await backend.create_container(request)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_secret_ref_without_resolver_fails_fast(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner([])
    )
    request = _request(
        tmp_path,
        environment=[{"name": "API_TOKEN", "secretRef": "db://api-token"}],
    )
    with pytest.raises(RuntimeError, match="secret"):
        await backend.create_container(request)


@pytest.mark.parametrize(
    "bad_args",
    [
        ["create", "--privileged"],
        ["create", "--privileged=true"],
        ["create", "--pid", "host"],
        ["create", "--ipc=host"],
        ["create", "--userns=host"],
        ["create", "--device", "/dev/kmsg"],
        ["create", "--mount", "type=bind,src=/var/run/docker.sock,dst=/x"],
        ["create", "--mount", "type=bind,src=/var/lib/docker,dst=/x"],
    ],
)
def test_final_launch_boundary_rejects_forbidden_args(bad_args) -> None:
    with pytest.raises(RuntimeError):
        DockerContainerJobBackend._reject_forbidden_launch_args(bad_args)


def test_final_launch_boundary_allows_hardened_baseline() -> None:
    # The safe hardened baseline must not trip the forbidden-flag check.
    DockerContainerJobBackend._reject_forbidden_launch_args(
        [
            "create",
            "--privileged=false",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--network",
            "none",
            "--mount",
            "type=bind,src=/work/agent_jobs/ws,dst=/workspace",
        ]
    )


@pytest.mark.asyncio
async def test_application_args_named_like_docker_flags_are_allowed(tmp_path) -> None:
    (tmp_path / "art_workspace").mkdir()
    commands: list[tuple[str, ...]] = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=_recording_runner(commands)
    )
    await backend.create_container(
        _request(tmp_path, command=["pytest", "--device", "cpu", "--pid", "42"])
    )
    assert any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_publish_evidence_bounds_captured_output(tmp_path) -> None:
    small = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_BYTES": "1024"}
    )
    captured: dict[str, bytes] = {}

    async def runner(args):
        if args[0] == "logs":
            return 0, b"x" * 5000, b""
        return 0, b"", b""

    async def publish(request, name, payload):
        captured[name] = payload
        return "art:logs"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=small,
        command_runner=runner,
        evidence_publisher=publish,
    )
    result = await backend.publish_evidence(_request(tmp_path))
    assert result.logs_ref == "art:logs"
    payload = captured[f"{JOB_ID}-logs.txt"]
    assert payload.startswith(b"[truncated]\n")
    assert len(payload) <= 1024 + len(b"[truncated]\n")
    # Deterministic per-stream artifacts are published alongside the combined log.
    assert f"{JOB_ID}-stdout.txt" in captured
    assert f"{JOB_ID}-stderr.txt" in captured


@pytest.mark.asyncio
async def test_create_failure_does_not_expose_resolved_workspace(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()

    async def runner(args):
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"missing"
        if args[0] == "create":
            return 1, b"", f"invalid bind src={workspace}".encode()
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    with pytest.raises(RuntimeError) as excinfo:
        await backend.create_container(_request(tmp_path))
    assert str(workspace) not in str(excinfo.value)
