"""Deployment-scoped native service access, without caller network authority."""

import json

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobSpec


def spec(**overrides):
    value = {
        "imageSourceRef": "asset-client",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "run-one"},
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "networkMode": "service",
        "serviceRef": "asset-store",
    }
    value.update(overrides)
    return ContainerJobSpec.model_validate(value)


def declaration():
    return {
        "serviceRef": "asset-store",
        "networkRef": "asset-internal",
        "gatewayRef": "asset-relay",
        "gatewayImage": "example/moonmind:latest",
        "egressNetworkRef": "asset-upstream",
        "serverName": "assets.example.test",
        "allowedImageSourceRefs": ["asset-client"],
        "relay": {
            "targetHost": "192.0.2.25",
            "targetPort": 41300,
            "listenHost": "172.30.7.2",
            "listenPort": 41300,
            "protocols": ["tcp", "udp"],
        },
    }


def settings(rows=None):
    return resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_SERVICE_CONNECTIONS": json.dumps(
                rows or [declaration()]
            ),
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "asset-client", "image": "example/client:latest"}]
            ),
        }
    )


def test_service_request_is_opaque_and_opt_in():
    request = spec()
    assert request.service_ref == "asset-store"
    with pytest.raises(ValueError, match="service"):
        spec(networkMode="none")
    with pytest.raises(ValueError, match="service"):
        spec(serviceRef=None)
    with pytest.raises(ValueError):
        spec(networkRef="host")
    with pytest.raises(ValueError, match="service"):
        spec(imageSourceRef=None, image="example/client:latest")
    assert "serviceRef" not in spec(networkMode="none", serviceRef=None).model_dump(
        by_alias=True, exclude_none=True
    )


def test_service_grant_is_deployment_owned_and_image_scoped():
    request = spec()
    assert settings().authorize_service(request).service_ref == "asset-store"
    with pytest.raises(ValueError, match="service"):
        resolve_container_backend_settings({}).authorize_service(request)
    with pytest.raises(ValueError, match="service"):
        settings().authorize_service(spec(imageSourceRef="other-image"))
    with pytest.raises(ValueError, match="service"):
        settings().authorize_service(spec(serviceRef="other-service"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("networkRef", "host"),
        ("serverName", "a,b"),
        ("allowedImageSourceRefs", []),
        ("arbitrary", True),
    ],
)
def test_invalid_declarations_fail_closed(field, value):
    row = declaration()
    row[field] = value
    with pytest.raises(ValueError):
        settings([row])


from unittest.mock import AsyncMock

from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.security.container_services import (
    SERVICE_DIGEST_LABEL,
    SERVICE_LABEL,
    attest_service_connection,
    attest_service_workload,
)
from moonmind.security.service_relay import IMPLEMENTATION_DIGEST
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend


def observed(connection):
    return {
        "network": {
            "Driver": "bridge",
            "Internal": True,
            "EnableIPv6": False,
            "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            "Containers": {"gateway-id": {}},
        },
        "gateway": {
            "id": "gateway-id",
            "networks": {
                connection.network_ref: {"IPAddress": connection.relay.listen_host},
                connection.egress_network_ref: {},
            },
            "image": "sha256:" + "b" * 64,
            "health": "healthy",
            "config": {
                "Image": connection.gateway_image,
                "Entrypoint": ["python", "-m", "moonmind.security.service_relay"],
                "Cmd": ["--config", "/run/moonmind-service/config.json"],
                "WorkingDir": "/app",
                "Env": [],
            },
            "mounts": [
                {"Destination": "/run/moonmind-service/config.json", "RW": False}
            ],
            "host": {
                "Privileged": False,
                "ReadonlyRootfs": True,
                "CapAdd": [],
                "CapDrop": ["ALL"],
            },
        },
        "health": {
            "configDigest": connection.relay.digest,
            "implementationDigest": IMPLEMENTATION_DIGEST,
        },
        "forwarding": b"0\n",
        "workload": {
            "image": "sha256:" + "a" * 64,
            "imageRef": "sha256:" + "a" * 64,
            "networks": {connection.network_ref: {}},
            "labels": {
                SERVICE_LABEL: connection.service_ref,
                SERVICE_DIGEST_LABEL: connection.digest,
            },
            "host": {
                "Privileged": False,
                "CapAdd": [],
                "CapDrop": ["ALL"],
                "Dns": ["127.0.0.1"],
                "ExtraHosts": [
                    f"{connection.server_name}:{connection.relay.listen_host}"
                ],
            },
        },
    }


def runner_for(connection, state, commands):
    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("network", "inspect"):
            return 0, json.dumps(state["network"]).encode(), b""
        if args[:2] == ("exec", connection.gateway_ref):
            if "--check" in args:
                return 0, json.dumps(state["health"]).encode(), b""
            return 0, state["forwarding"], b""
        if args[:2] == ("inspect", "--format"):
            if args[-1] == connection.gateway_ref:
                return 0, json.dumps(state["gateway"]).encode(), b""
            if args[2] == "{{json .Config.Labels}}":
                return 1, b"", b"no such container"
            if args[2] == "{{json .State}}":
                return 0, b'{"Status":"exited","Running":false,"ExitCode":0}', b""
            return 0, json.dumps(state["workload"]).encode(), b""
        return 0, b"", b""

    return runner


@pytest.mark.asyncio
async def test_actual_attestation_requires_loaded_config_and_enforcement():
    connection = settings().authorize_service(spec())
    state = observed(connection)
    evidence = await attest_service_connection(
        runner_for(connection, state, []), connection
    )
    assert evidence["applicationReadOnly"] is False
    assert "192.0.2.25" not in json.dumps(evidence)
    await attest_service_workload(
        runner_for(connection, state, []), connection, "job", state["workload"]["image"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "not_internal",
        "ipv6",
        "host_gateway",
        "wrong_ip",
        "unhealthy",
        "privileged",
        "extra_network",
        "forwarding",
        "config",
        "implementation",
        "unknown_peer",
        "gateway_command",
        "writable_config",
        "python_path",
    ],
)
async def test_service_attestation_rejects_drift_and_forgery(fault):
    connection = settings().authorize_service(spec())
    state = observed(connection)
    if fault == "not_internal":
        state["network"]["Internal"] = False
    if fault == "ipv6":
        state["network"]["EnableIPv6"] = True
    if fault == "host_gateway":
        state["network"]["Options"].clear()
    if fault == "wrong_ip":
        state["gateway"]["networks"][connection.network_ref]["IPAddress"] = "172.30.7.3"
    if fault == "unhealthy":
        state["gateway"]["health"] = "unhealthy"
    if fault == "privileged":
        state["gateway"]["host"]["Privileged"] = True
    if fault == "extra_network":
        state["gateway"]["networks"]["control-plane"] = {}
    if fault == "forwarding":
        state["forwarding"] = b"1\n"
    if fault == "config":
        state["health"]["configDigest"] = "sha256:" + "c" * 64
    if fault == "implementation":
        state["health"]["implementationDigest"] = "invalid evidence"
    if fault == "unknown_peer":
        state["network"]["Containers"]["unknown"] = {}
    if fault == "gateway_command":
        state["gateway"]["config"]["Cmd"] = ["-c", "other proxy"]
    if fault == "writable_config":
        state["gateway"]["mounts"][0]["RW"] = True
    if fault == "python_path":
        state["gateway"]["config"]["Env"] = ["PYTHONPATH=/tmp"]
    with pytest.raises(RuntimeError, match="service"):
        await attest_service_connection(runner_for(connection, state, []), connection)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["extra_network", "dns", "alias", "image", "policy", "privileged"]
)
async def test_service_workload_rejects_authority_drift(fault):
    connection = settings().authorize_service(spec())
    state = observed(connection)
    job = state["workload"]
    expected_image = job["image"]
    if fault == "extra_network":
        job["networks"]["bridge"] = {}
    if fault == "dns":
        job["host"]["Dns"] = ["100.100.100.100"]
    if fault == "alias":
        job["host"]["ExtraHosts"] = []
    if fault == "image":
        job["image"] = "sha256:" + "f" * 64
    if fault == "policy":
        job["labels"][SERVICE_DIGEST_LABEL] = "other"
    if fault == "privileged":
        job["host"]["Privileged"] = True
    with pytest.raises(RuntimeError, match="service"):
        await attest_service_workload(
            runner_for(connection, state, []), connection, "job", expected_image
        )


def activity(tmp_path):
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": "container-job:" + "0" * 32,
            "ownershipToken": "job:owned",
            "request": {
                "idempotencyKey": "service-one",
                "source": {"source": "workflow", "workflowId": "wf-one"},
                "spec": spec().model_dump(by_alias=True, exclude_none=True),
            },
            "resolvedWorkspaceRef": str(tmp_path / "run-one"),
            "resolvedImageRef": "sha256:" + "a" * 64,
        }
    )


@pytest.mark.asyncio
async def test_backend_service_launch_scoped_workspace_and_receipt(tmp_path):
    policy = settings()
    connection = policy.authorize_service(spec())
    state, commands = observed(connection), []
    publisher = AsyncMock(return_value="artifact:service")
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=policy,
        command_runner=runner_for(connection, state, commands),
        evidence_publisher=publisher,
    )
    result = await backend.create_container(activity(tmp_path))
    create = next(c for c in commands if c[0] == "create")
    assert create[create.index("--network") + 1] == connection.network_ref
    assert create[create.index("--dns") + 1] == "127.0.0.1"
    assert create[create.index("--add-host") + 1] == "assets.example.test:172.30.7.2"
    assert f"type=bind,src={tmp_path / 'run-one'},dst=/workspace" in create
    assert "HTTP_PROXY" not in " ".join(create)
    assert "MOONMIND_SERVICE_HOST=assets.example.test" in create
    assert result.diagnostics_ref == "artifact:service"
    assert not any(
        command[0] in {"start", "network"} and command[1:2] == ("create",)
        for command in commands
    )


@pytest.mark.asyncio
async def test_backend_rejects_missing_grant_before_any_docker_operation(tmp_path):
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=AsyncMock(),
        evidence_publisher=AsyncMock(),
    )
    with pytest.raises(ValueError, match="service"):
        await backend.create_container(activity(tmp_path))
    backend._runner.assert_not_called()


@pytest.mark.asyncio
async def test_failed_service_receipt_removes_only_created_job(tmp_path):
    policy = settings()
    connection = policy.authorize_service(spec())
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=policy,
        command_runner=runner_for(connection, observed(connection), commands),
        evidence_publisher=AsyncMock(side_effect=RuntimeError("artifact failure")),
    )
    request = activity(tmp_path)
    with pytest.raises(RuntimeError, match="artifact"):
        await backend.create_container(request)
    assert ("rm", "--force", backend._name(request)) in commands
    assert not any(connection.gateway_ref in c for c in commands if c[0] == "rm")


@pytest.mark.asyncio
async def test_cleanup_still_works_after_service_grant_revoked(tmp_path):
    commands = []

    async def runner(args):
        commands.append(tuple(args))
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=AsyncMock(return_value="artifact:cleanup"),
    )
    result = await backend.cleanup(activity(tmp_path))
    assert result.cleanup_succeeded is True
    assert commands[0][:2] == ("ps", "-aq")
    assert any("label=moonmind.ownership=job:owned" == part for part in commands[0])


@pytest.mark.asyncio
async def test_api_service_denies_unconfigured_grant_before_persistence():
    from api_service.services.container_jobs import ContainerJobService
    from moonmind.schemas.container_job_models import (
        ContainerJobSubmitRequest,
        OwnerIdentity,
    )

    session, temporal = AsyncMock(), AsyncMock()
    service = ContainerJobService(
        session,
        temporal=temporal,
        backend_settings=resolve_container_backend_settings({}),
    )
    request = ContainerJobSubmitRequest(
        idempotencyKey="deny-first",
        source={"source": "workflow", "workflowId": "wf"},
        spec=spec(),
    )
    with pytest.raises(ValueError, match="service"):
        await service.submit(
            owner=OwnerIdentity(principalId="operator", principalType="user"),
            request=request,
        )
    session.commit.assert_not_called()
    temporal.start_container_job.assert_not_called()


@pytest.mark.asyncio
async def test_capacity_retry_revalidates_relay_before_start(tmp_path):
    policy = settings()
    connection = policy.authorize_service(spec())
    state, commands = observed(connection), []
    state["health"]["configDigest"] = "sha256:" + "c" * 64
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=policy,
        command_runner=runner_for(connection, state, commands),
        evidence_publisher=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="mismatched"):
        await backend.start_container(activity(tmp_path))
    assert not any(c[0] == "start" for c in commands)


@pytest.mark.asyncio
async def test_recovered_job_reuses_evidence_without_starting_again(tmp_path):
    policy = settings()
    connection = policy.authorize_service(spec())
    commands = []
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=policy,
        command_runner=runner_for(connection, observed(connection), commands),
        evidence_publisher=AsyncMock(return_value="artifact:recovered"),
    )
    result = await backend._recover_launch_attestation(
        activity(tmp_path), "job", running=False
    )
    assert result == "artifact:recovered"
    assert not any(c[0] in {"start", "create"} for c in commands)


@pytest.mark.asyncio
async def test_registry_manifest_reference_is_distinct_from_image_configuration():
    connection = settings().authorize_service(spec())
    state = observed(connection)
    state["workload"]["imageRef"] = "registry.example/client@sha256:" + "d" * 64
    evidence = await attest_service_workload(
        runner_for(connection, state, []),
        connection,
        "job",
        state["workload"]["imageRef"],
    )
    assert evidence["workloadImageDigest"] == "sha256:" + "a" * 64


def test_service_dns_exception_never_weakens_normal_launch_policy():
    with pytest.raises(RuntimeError, match="forbidden"):
        DockerContainerJobBackend._reject_forbidden_launch_args(["--dns", "127.0.0.1"])
    with pytest.raises(RuntimeError, match="deployment"):
        DockerContainerJobBackend._reject_forbidden_launch_args(
            ["--dns", "8.8.8.8", "--add-host", "service:172.30.7.2"],
            expected_service_host="service:172.30.7.2",
        )
    with pytest.raises(RuntimeError, match="deployment"):
        DockerContainerJobBackend._reject_forbidden_launch_args(
            [
                "--dns",
                "127.0.0.1",
                "--dns",
                "8.8.8.8",
                "--add-host",
                "service:172.30.7.2",
            ],
            expected_service_host="service:172.30.7.2",
        )


@pytest.mark.asyncio
async def test_service_relay_independent_source_update_preserves_interface():
    connection = settings().authorize_service(spec())
    state = observed(connection)
    state["health"]["implementationDigest"] = "sha256:" + "c" * 64
    evidence = await attest_service_connection(
        runner_for(connection, state, []), connection
    )
    assert evidence["implementationDigest"] == state["health"]["implementationDigest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [True, False])
async def test_disappearing_peer_requires_confirmed_network_removal(removed):
    connection = settings().authorize_service(spec())
    state = observed(connection)
    state["network"]["Containers"]["finishing-peer"] = {}
    original = runner_for(connection, state, [])
    inspections = 0

    async def runner(args):
        nonlocal inspections
        if args[0] == "inspect" and args[-1] == "finishing-peer":
            inspections += 1
            if removed:
                state["network"]["Containers"].pop("finishing-peer", None)
            return 1, b"", b"container unavailable"
        return await original(args)

    if removed:
        await attest_service_connection(runner, connection)
    else:
        with pytest.raises(RuntimeError, match="peer"):
            await attest_service_connection(runner, connection)
    assert inspections == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["new-peer", "network", "second-removal"])
async def test_peer_reconciliation_rechecks_boundary_and_is_bounded(drift):
    connection = settings().authorize_service(spec())
    state = observed(connection)
    state["network"]["Containers"]["first"] = {}
    original = runner_for(connection, state, [])
    failures = 0

    async def runner(args):
        nonlocal failures
        if args[0] == "inspect" and args[-1] in {"first", "second"}:
            failures += 1
            state["network"]["Containers"].pop(args[-1], None)
            if drift == "new-peer":
                state["network"]["Containers"]["unauthorized"] = {}
            elif drift == "network":
                state["network"]["Internal"] = False
            elif args[-1] == "first":
                state["network"]["Containers"]["second"] = {}
            return 1, b"", b"not found"
        return await original(args)

    with pytest.raises(RuntimeError, match="service"):
        await attest_service_connection(runner, connection)
    assert failures == (2 if drift == "second-removal" else 1)
