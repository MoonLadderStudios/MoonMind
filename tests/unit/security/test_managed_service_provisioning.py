"""Cold and retained fixed-service resources through the real daemon boundary."""

import asyncio
import copy
import json

import pytest

from moonmind.security import container_services as services
from moonmind.security.service_relay import IMPLEMENTATION_DIGEST


def connection():
    return services.ServiceConnection.from_mapping(
        {
            "serviceRef": "asset-store",
            "networkRef": "asset-internal",
            "gatewayRef": "asset-relay",
            "gatewayImage": "example/moonmind:latest",
            "egressNetworkRef": "asset-upstream",
            "serverName": "assets.example.test",
            "allowedImageSourceRefs": ["asset-client"],
            "provision": True,
            "relay": {
                "targetHost": "192.0.2.25",
                "targetPort": 41300,
                "listenHost": "172.30.7.2",
                "listenPort": 41300,
                "protocols": ["tcp", "udp"],
            },
        }
    )


def options(args, name):
    return [args[i + 1] for i, value in enumerate(args[:-1]) if value == name]


class Daemon:
    def __init__(self, policy):
        self.policy = policy
        self.commands = []
        self.networks = {}
        self.gateway = None
        self.image = False
        self.failure = None
        self.log_output = b"original relay startup failure"

    async def __call__(self, args):
        args = tuple(args)
        self.commands.append(args)
        await asyncio.sleep(0)  # Permit genuinely overlapping first-use requests.
        if self.failure and args[: len(self.failure[0])] == self.failure[0]:
            return 1, b"", self.failure[1]
        if args[:2] == ("network", "inspect"):
            value = self.networks.get(args[-1])
            return self.result(value, "No such network")
        if args[:2] == ("network", "create"):
            name = args[-1]
            if name in self.networks:
                return 1, b"", b"network already exists"
            self.networks[name] = {
                "Driver": "bridge",
                "Internal": "--internal" in args,
                "EnableIPv6": False,
                "Labels": dict(
                    value.split("=", 1) for value in options(args, "--label")
                ),
                "Options": dict(
                    value.split("=", 1) for value in options(args, "--opt")
                ),
                "IPAM": {
                    "Config": [{"Subnet": value} for value in options(args, "--subnet")]
                },
                "Containers": {},
            }
            return 0, b"network-id", b""
        if args[:2] == ("image", "inspect"):
            return self.result(
                {"Id": "sha256:" + "a" * 64} if self.image else None, "No such image"
            )
        if args[0] == "pull":
            self.image = True
            return 0, b"pulled", b""
        if args[0] == "inspect":
            return self.result(self.gateway, "No such container")
        if args[0] == "create":
            if self.gateway is not None:
                return 1, b"", b"container name already exists"
            labels = dict(value.split("=", 1) for value in options(args, "--label"))
            self.gateway = {
                "id": "gateway-id",
                "image": "sha256:" + "a" * 64,
                "health": "starting",
                "state": {"Running": False, "Status": "created"},
                "networks": {
                    self.policy.network_ref: {
                        "IPAddress": "",
                        "IPAMConfig": {"IPv4Address": self.policy.relay.listen_host},
                    },
                    self.policy.egress_network_ref: {},
                },
                "config": {
                    "Image": self.policy.gateway_image,
                    "Entrypoint": ["python"],
                    "Cmd": [
                        "-m",
                        "moonmind.security.service_relay",
                        "--config-env",
                        "MOONMIND_SERVICE_RELAY_CONFIG",
                    ],
                    "WorkingDir": "/app",
                    "Env": options(args, "--env"),
                    "Labels": labels,
                    "User": options(args, "--user")[0],
                },
                "mounts": [],
                "host": {
                    "Privileged": False,
                    "ReadonlyRootfs": "--read-only" in args,
                    "CapAdd": [],
                    "CapDrop": options(args, "--cap-drop"),
                    "SecurityOpt": options(args, "--security-opt"),
                    "Sysctls": dict(
                        value.split("=", 1) for value in options(args, "--sysctl")
                    ),
                },
            }
            self.networks[self.policy.network_ref]["Containers"]["gateway-id"] = {}
            return 0, b"gateway-id", b""
        if args[0] == "start":
            self.gateway["state"] = {"Running": True, "Status": "running"}
            self.gateway["networks"][self.policy.network_ref][
                "IPAddress"
            ] = self.policy.relay.listen_host
            self.gateway["health"] = "healthy"
            return 0, b"gateway-id", b""
        if args[0] == "exec":
            if "--check" in args:
                return self.result(
                    {
                        "configDigest": self.policy.relay.digest,
                        "implementationDigest": IMPLEMENTATION_DIGEST,
                    }
                )
            return 0, b"0\n", b""
        if args[0] == "logs":
            return 0, self.log_output, b""
        raise AssertionError(args)

    @staticmethod
    def result(value, failure=""):
        return (
            (0, json.dumps(value).encode(), b"")
            if value is not None
            else (1, b"", failure.encode())
        )


@pytest.mark.asyncio
async def test_cold_service_provisions_then_attests_and_warm_service_is_reused():
    policy = connection()
    daemon = Daemon(policy)
    receipt = await services.ensure_service_connection(daemon, policy)
    assert receipt["configDigest"] == policy.relay.digest
    assert receipt["applicationReadOnly"] is False
    create = next(c for c in daemon.commands if c[0] == "create")
    assert "--read-only" in create
    assert "--mount" not in create
    assert "--publish" not in create
    assert options(create, "--network") == [
        "name=asset-internal,ip=172.30.7.2",
        "name=asset-upstream",
    ]
    configuration = next(
        value
        for value in options(create, "--env")
        if value.startswith("MOONMIND_SERVICE_RELAY_CONFIG=")
    )
    assert json.loads(configuration.split("=", 1)[1])["protocols"] == ["tcp", "udp"]
    assert (
        next(c for c in daemon.commands if c[:2] == ("network", "create"))[-1]
        == "asset-internal"
    )
    daemon.commands.clear()
    assert await services.ensure_service_connection(daemon, policy) == receipt
    assert not any(
        c[0] in {"create", "start", "pull"} or c[:2] == ("network", "create")
        for c in daemon.commands
    )


@pytest.mark.asyncio
async def test_concurrent_cold_service_requests_share_one_relay():
    policy = connection()
    daemon = Daemon(policy)
    receipts = await asyncio.gather(
        *(services.ensure_service_connection(daemon, policy) for _ in range(4))
    )
    assert all(value == receipts[0] for value in receipts)
    assert len([c for c in daemon.commands if c[0] == "create"]) == 1
    assert len([c for c in daemon.commands if c[:2] == ("network", "create")]) == 2


@pytest.mark.asyncio
async def test_independent_workers_reconcile_committed_docker_name_conflicts():
    from moonmind.security.service_provisioning import _ensure

    policy = connection()
    daemon = Daemon(policy)
    await asyncio.gather(*(_ensure(daemon, policy) for _ in range(3)))
    assert (await services.attest_service_connection(daemon, policy))[
        "endpointAccess"
    ] is True
    assert not any(c[0] == "rm" or c[:2] == ("network", "rm") for c in daemon.commands)


@pytest.mark.asyncio
async def test_inspection_transport_failure_does_not_create_replacement_resources():
    policy = connection()
    daemon = Daemon(policy)
    daemon.failure = ("network", "inspect"), b"daemon unavailable"
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        await services.ensure_service_connection(daemon, policy)
    assert all(c[:2] == ("network", "inspect") for c in daemon.commands)


@pytest.mark.asyncio
async def test_stopped_relay_restarts_without_replacing_saved_resources():
    policy = connection()
    daemon = Daemon(policy)
    await services.ensure_service_connection(daemon, policy)
    original_id = daemon.gateway["id"]
    daemon.gateway["state"] = {"Running": False, "Status": "exited"}
    daemon.gateway["networks"][policy.network_ref]["IPAddress"] = ""
    daemon.commands.clear()
    await services.ensure_service_connection(daemon, policy)
    assert daemon.gateway["id"] == original_id
    assert [
        c for c in daemon.commands if c[0] in {"create", "start", "pull", "rm"}
    ] == [("start", policy.gateway_ref)]


@pytest.mark.asyncio
async def test_unhealthy_relay_preserves_startup_diagnostic_and_resources():
    policy = connection()
    daemon = Daemon(policy)
    await services.ensure_service_connection(daemon, policy)
    daemon.gateway["health"] = "unhealthy"
    daemon.commands.clear()
    with pytest.raises(RuntimeError, match="original relay startup failure"):
        await services.ensure_service_connection(daemon, policy)
    assert not any(c[0] in {"start", "rm", "create"} for c in daemon.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["unhealthy", "start"])
async def test_failed_relay_returns_bounded_redacted_startup_diagnostics(phase):
    policy = connection()
    daemon = Daemon(policy)
    daemon.log_output = b"unknown --config-env\napi_key=private-credential-value\n"
    if phase == "unhealthy":
        await services.ensure_service_connection(daemon, policy)
        daemon.gateway["health"] = "unhealthy"
    else:
        daemon.failure = ("start",), b"original daemon start failure"
    with pytest.raises(RuntimeError) as raised:
        await services.ensure_service_connection(daemon, policy)
    message = str(raised.value)
    assert "unknown --config-env" in message
    assert "private-credential-value" not in message
    assert ("logs", "--tail", "50", policy.gateway_ref) in daemon.commands
    if phase == "start":
        assert "original daemon start failure" in message


@pytest.mark.asyncio
async def test_failed_start_keeps_original_error_when_log_collection_raises():
    policy = connection()
    daemon = Daemon(policy)
    daemon.failure = ("start",), b"original daemon start failure"

    async def runner(args):
        if args[0] == "logs":
            raise OSError("diagnostics transport unavailable")
        return await daemon(args)

    with pytest.raises(RuntimeError, match="original daemon start failure") as raised:
        await services.ensure_service_connection(runner, policy)
    assert "diagnostics could not be collected" in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "unowned-network",
        "network-policy",
        "unowned-relay",
        "relay-config",
        "extra-mount",
        "tmp-bind",
        "root-user",
        "new-privileges",
        "routing",
    ],
)
async def test_existing_resource_drift_is_never_deleted_or_repaired(fault):
    policy = connection()
    daemon = Daemon(policy)
    await services.ensure_service_connection(daemon, policy)
    if fault == "unowned-network":
        daemon.networks[policy.network_ref]["Labels"].clear()
    elif fault == "network-policy":
        daemon.networks[policy.network_ref]["Internal"] = False
    elif fault == "unowned-relay":
        daemon.gateway["config"]["Labels"].clear()
    elif fault == "relay-config":
        daemon.gateway["config"]["Env"] = ["MOONMIND_SERVICE_RELAY_CONFIG={}"]
    elif fault == "tmp-bind":
        daemon.gateway["mounts"].append(
            {"Destination": "/tmp", "Type": "bind", "RW": True}
        )
    elif fault == "root-user":
        daemon.gateway["config"]["User"] = "0"
    elif fault == "new-privileges":
        daemon.gateway["host"]["SecurityOpt"] = []
    elif fault == "routing":
        daemon.gateway["host"]["Sysctls"]["net.ipv4.ip_forward"] = "1"
    else:
        daemon.gateway["mounts"].append({"Destination": "/app", "RW": True})
    before = copy.deepcopy((daemon.networks, daemon.gateway))
    daemon.commands.clear()
    with pytest.raises(RuntimeError, match="service"):
        await services.ensure_service_connection(daemon, policy)
    assert (daemon.networks, daemon.gateway) == before
    assert not any(
        c[0] in {"create", "start", "rm", "pull"}
        or c[:2] in {("network", "rm"), ("network", "create")}
        for c in daemon.commands
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", [("pull",), ("network", "create"), ("create",), ("start",)]
)
async def test_provisioning_failure_retains_original_diagnostic_and_resources(
    operation,
):
    policy = connection()
    daemon = Daemon(policy)
    daemon.failure = operation, b"original daemon failure"
    with pytest.raises(RuntimeError, match="original daemon failure"):
        await services.ensure_service_connection(daemon, policy)
    assert not any(c[0] == "rm" or c[:2] == ("network", "rm") for c in daemon.commands)
    daemon.failure = None
    assert (await services.ensure_service_connection(daemon, policy))[
        "endpointAccess"
    ] is True


def test_provisioning_subnet_must_contain_a_usable_relay_address():
    row = connection()
    assert row.subnet == "172.30.7.0/24"
    for subnet in ("172.31.0.0/24", "172.30.7.2/32", "0.0.0.0/0", "not-a-subnet"):
        with pytest.raises(ValueError):
            services.ServiceConnection.from_mapping(
                {
                    "serviceRef": row.service_ref,
                    "networkRef": row.network_ref,
                    "gatewayRef": row.gateway_ref,
                    "gatewayImage": row.gateway_image,
                    "egressNetworkRef": row.egress_network_ref,
                    "serverName": row.server_name,
                    "allowedImageSourceRefs": list(row.allowed_image_source_refs),
                    "relay": row.relay.to_mapping(),
                    "provision": True,
                    "subnet": subnet,
                }
            )
