"""Opt-in deployment service connections for canonical container jobs.

The trusted relay owns a fixed upstream. A job selects an opaque grant, never a
host network or destination. This is endpoint access, not application read-only
permission; the deployment must separately restrict its upstream when needed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from moonmind.security.service_relay import RelayConfig

SERVICE_CONNECTIONS_ENV_KEY = "MOONMIND_CONTAINER_BACKEND_SERVICE_CONNECTIONS"
SERVICE_LABEL = "moonmind.service.ref"
SERVICE_DIGEST_LABEL = "moonmind.service.digest"
RELAY_CONFIG_PATH = "/run/moonmind-service/config.json"
RELAY_CONFIG_ENV = "MOONMIND_SERVICE_RELAY_CONFIG"
MANAGED_SERVICE_LABEL = "moonmind.service.managed"
GATEWAY_INSPECT_FORMAT = '{"networks":{{json .NetworkSettings.Networks}},"image":{{json .Image}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},"state":{{json .State}},"host":{{json .HostConfig}},"config":{{json .Config}},"mounts":{{json .Mounts}},"id":{{json .Id}}}'
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_REF = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,126}[a-z0-9])?$")
_DNS = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
_RESERVED_NETWORKS = {"host", "none", "bridge"}
CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]


def _digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


@dataclass(frozen=True)
class ServiceConnection:
    service_ref: str
    network_ref: str
    gateway_ref: str
    gateway_image: str
    egress_network_ref: str
    server_name: str
    allowed_image_source_refs: tuple[str, ...]
    relay: RelayConfig
    provision: bool = False
    subnet: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ServiceConnection:
        required = {
            "serviceRef",
            "networkRef",
            "gatewayRef",
            "gatewayImage",
            "egressNetworkRef",
            "serverName",
            "allowedImageSourceRefs",
            "relay",
        }
        optional = {"provision", "subnet"}
        if (
            not isinstance(value, Mapping)
            or required - set(value)
            or set(value) - required - optional
        ):
            raise ValueError("service declaration has missing or unsupported fields")
        names = [value[k] for k in ("networkRef", "gatewayRef", "egressNetworkRef")]
        if any(
            not isinstance(v, str) or not _NAME.fullmatch(v) or v in _RESERVED_NETWORKS
            for v in names
        ):
            raise ValueError("service declaration has invalid deployment identities")
        if names[0] == names[2]:
            raise ValueError("service network must be separate from upstream network")
        gateway_image = value["gatewayImage"]
        if (
            not isinstance(gateway_image, str)
            or not gateway_image
            or gateway_image.startswith("-")
            or len(gateway_image) > 512
            or any(char.isspace() for char in gateway_image)
        ):
            raise ValueError("service relay image is invalid")
        ref = value["serviceRef"]
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise ValueError("service reference is invalid")
        server = value["serverName"]
        if (
            not isinstance(server, str)
            or not _DNS.fullmatch(server)
            or any(
                not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
                for part in server.split(".")
            )
        ):
            raise ValueError("service serverName must be one DNS hostname")
        try:
            ipaddress.ip_address(server)
        except ValueError:
            pass
        else:
            raise ValueError("service serverName must preserve a DNS identity")
        refs = value["allowedImageSourceRefs"]
        if (
            not isinstance(refs, list)
            or not 1 <= len(refs) <= 32
            or any(not isinstance(v, str) or not _REF.fullmatch(v) for v in refs)
            or len(set(refs)) != len(refs)
        ):
            raise ValueError("service declaration needs explicit image source grants")
        relay = RelayConfig.from_mapping(value["relay"])
        if any(
            ipaddress.IPv4Address(value).is_loopback
            or ipaddress.IPv4Address(value).is_link_local
            for value in (relay.listen_host, relay.target_host)
        ):
            raise ValueError(
                "service endpoint cannot target loopback or link-local services"
            )
        if relay.listen_port != relay.target_port:
            raise ValueError("service relay must preserve the endpoint port")
        provision = value.get("provision", False)
        if not isinstance(provision, bool):
            raise TypeError("service provisioning must be an explicit boolean")
        subnet = None
        if provision:
            network = ipaddress.IPv4Network(
                value.get("subnet") or f"{relay.listen_host}/24", strict=False
            )
            address = ipaddress.IPv4Address(relay.listen_host)
            if (
                not 16 <= network.prefixlen <= 30
                or address not in network
                or address
                in {
                    network.network_address,
                    network.broadcast_address,
                    network.network_address + 1,
                }
            ):
                raise ValueError(
                    "service subnet must contain a usable non-gateway relay address"
                )
            subnet = str(network)
        elif "subnet" in value:
            raise ValueError("service subnet requires managed provisioning")
        return cls(
            ref,
            names[0],
            names[1],
            gateway_image,
            names[2],
            server.lower(),
            tuple(sorted(refs)),
            relay,
            provision,
            subnet,
        )

    @property
    def digest(self) -> str:
        policy = {
            "serviceRef": self.service_ref,
            "networkRef": self.network_ref,
            "gatewayRef": self.gateway_ref,
            "gatewayImage": self.gateway_image,
            "egressNetworkRef": self.egress_network_ref,
            "serverName": self.server_name,
            "allowedImageSourceRefs": self.allowed_image_source_refs,
            "relayDigest": self.relay.digest,
        }
        if self.provision:
            policy.update(provision=True, subnet=self.subnet)
        return _digest(policy)


def parse_service_connections(raw: str | None) -> tuple[ServiceConnection, ...]:
    if not raw or not raw.strip():
        return ()
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("service declarations must be a bounded array")
        result = tuple(ServiceConnection.from_mapping(row) for row in rows)
        if len({v.service_ref for v in result}) != len(result) or len(
            {v.network_ref for v in result}
        ) != len(result):
            raise ValueError(
                "service declarations must have distinct references and networks"
            )
        return result
    except (ValueError, TypeError, KeyError) as exc:
        # Configuration diagnostics may reach the agent. Never echo topology.
        raise ValueError("container service declaration is invalid") from exc


async def _json(runner: CommandRunner, args: Sequence[str], failure: str) -> dict:
    code, stdout, _ = await runner(args)
    if code:
        raise RuntimeError(failure)
    try:
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise TypeError("expected object")
        return value
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError(failure) from exc


def _configuration_arguments(connection: ServiceConnection) -> list[str]:
    return (
        ["--config-env", RELAY_CONFIG_ENV]
        if connection.provision
        else ["--config", RELAY_CONFIG_PATH]
    )


def validate_relay_launch(
    connection: ServiceConnection,
    gateway: Mapping[str, Any],
    *,
    allow_stopped: bool = False,
) -> None:
    """Validate a stopped or running relay before any lifecycle mutation."""
    config = gateway.get("config") or {}
    host = gateway.get("host") or {}
    networks = gateway.get("networks") or {}
    internal = networks.get(connection.network_ref) or {}
    address = internal.get("IPAddress")
    if allow_stopped and (gateway.get("state") or {}).get("Running") is False:
        address = address or (internal.get("IPAMConfig") or {}).get("IPv4Address")
    mounts = gateway.get("mounts")
    valid_config = False
    if connection.provision:
        values = [
            value.split("=", 1)[1]
            for value in config.get("Env") or []
            if isinstance(value, str) and value.startswith(RELAY_CONFIG_ENV + "=")
        ]
        try:
            valid_config = (
                len(values) == 1
                and RelayConfig.from_mapping(json.loads(values[0])).digest
                == connection.relay.digest
            )
        except (ValueError, TypeError, KeyError):
            # Malformed relay config uses the shared launch validation error below.
            valid_config = False
        valid_mounts = isinstance(mounts, list) and all(
            m.get("Destination") == "/tmp" and m.get("Type") == "tmpfs" for m in mounts
        )
        if (
            config.get("User") != "1000:1000"
            or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
            or (host.get("Sysctls") or {}).get("net.ipv4.ip_forward") != "0"
        ):
            raise RuntimeError("service relay managed execution policy is invalid")
    else:
        valid_config = True
        valid_mounts = (
            isinstance(mounts, list)
            and any(
                m.get("Destination") == RELAY_CONFIG_PATH and m.get("RW") is False
                for m in mounts
            )
            and all(
                m.get("Destination") in {RELAY_CONFIG_PATH, "/tmp"}
                and (m.get("Destination") != RELAY_CONFIG_PATH or m.get("RW") is False)
                for m in mounts
            )
        )
    if (
        config.get("Image") != connection.gateway_image
        or config.get("Entrypoint")
        != (
            ["python"]
            if connection.provision
            else ["python", "-m", "moonmind.security.service_relay"]
        )
        or config.get("Cmd")
        != (
            (["-m", "moonmind.security.service_relay"] if connection.provision else [])
            + _configuration_arguments(connection)
        )
        or config.get("WorkingDir") != "/app"
        or any(
            str(value).split("=", 1)[0]
            in {"PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH"}
            for value in config.get("Env") or []
        )
        or not valid_config
        or not valid_mounts
    ):
        raise RuntimeError("service relay launch or mounted configuration is invalid")
    if (
        set(networks) != {connection.network_ref, connection.egress_network_ref}
        or address != connection.relay.listen_host
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(gateway.get("image", "")))
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapAdd")
        or "ALL" not in (host.get("CapDrop") or [])
    ):
        raise RuntimeError("service relay identity or isolation is invalid")


async def ensure_service_connection(
    runner: CommandRunner, connection: ServiceConnection
) -> dict:
    """Prepare opted-in shared resources, then attest the actual boundary."""
    if connection.provision:
        from moonmind.security.service_provisioning import provision_service_connection

        await provision_service_connection(runner, connection)
    return await attest_service_connection(runner, connection)


async def attest_service_connection(
    runner: CommandRunner,
    connection: ServiceConnection,
    *,
    _remaining_reconciliations: int = 1,
) -> dict:
    network = await _json(
        runner,
        ("network", "inspect", "--format", "{{json .}}", connection.network_ref),
        "service network is unavailable",
    )
    if (
        network.get("Driver") != "bridge"
        or network.get("Internal") is not True
        or network.get("EnableIPv6") is not False
        or (network.get("Options") or {}).get(
            "com.docker.network.bridge.gateway_mode_ipv4"
        )
        != "isolated"
    ):
        raise RuntimeError("service network must be isolated internal IPv4-only state")
    gateway = await _json(
        runner,
        (
            "container",
            "inspect",
            "--format",
            GATEWAY_INSPECT_FORMAT,
            connection.gateway_ref,
        ),
        "service relay is unavailable",
    )
    validate_relay_launch(connection, gateway)
    if gateway.get("health") != "healthy":
        raise RuntimeError("service relay health is invalid")
    # The relay cannot act as a packet router even if another workload crafts
    # routes to it. It has no NET_ADMIN and forwarding is explicitly disabled.
    code, forwarding, _ = await runner(
        ("exec", connection.gateway_ref, "cat", "/proc/sys/net/ipv4/ip_forward")
    )
    if code or forwarding.strip() != b"0":
        raise RuntimeError("service relay must not forward IP packets")
    observed = await _json(
        runner,
        (
            "exec",
            connection.gateway_ref,
            "python",
            "-m",
            "moonmind.security.service_relay",
            "--check",
            *_configuration_arguments(connection),
        ),
        "service relay live configuration is unavailable",
    )
    # The deployment-authorized image, canonical startup and read-only code
    # establish executable integrity. Record the running implementation, but do
    # not couple independently updated worker and relay source revisions.
    implementation = observed.get("implementationDigest")
    if (
        observed.get("configDigest") != connection.relay.digest
        or not isinstance(implementation, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", implementation)
    ):
        raise RuntimeError("service relay live configuration or evidence is mismatched")
    peers = network.get("Containers") or {}
    if not isinstance(peers, dict) or gateway.get("id") not in peers:
        raise RuntimeError("service relay network membership is unavailable")
    for peer_id in peers:
        if peer_id == gateway["id"]:
            continue
        fmt = '{"labels":{{json .Config.Labels}},"networks":{{json .NetworkSettings.Networks}}}'
        try:
            peer = await _json(
                runner,
                ("container", "inspect", "--format", fmt, peer_id),
                "service network peer is unavailable",
            )
        except RuntimeError:
            if not _remaining_reconciliations:
                raise
            current = await _json(
                runner,
                (
                    "network",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    connection.network_ref,
                ),
                "service network reconciliation is unavailable",
            )
            membership = current.get("Containers")
            if not isinstance(membership, dict) or peer_id in membership:
                raise
            # Confirmed concurrent removal is not an isolation violation. Start
            # a bounded fresh attestation so new peers and network drift are
            # checked too; an inspect error alone never authorizes a peer.
            return await attest_service_connection(
                runner,
                connection,
                _remaining_reconciliations=_remaining_reconciliations - 1,
            )
        labels = peer.get("labels") or {}
        if (
            labels.get(SERVICE_LABEL) != connection.service_ref
            or labels.get(SERVICE_DIGEST_LABEL) != connection.digest
            or not labels.get("moonmind.container_job")
            or not labels.get("moonmind.ownership")
            or set(peer.get("networks") or {}) != {connection.network_ref}
        ):
            raise RuntimeError("service network contains an unapproved peer")
    return {
        "kind": "fixed-service-relay",
        "serviceRef": connection.service_ref,
        "policyDigest": connection.digest,
        "configDigest": connection.relay.digest,
        "implementationDigest": implementation,
        "gatewayImageDigest": gateway["image"],
        "endpointAccess": True,
        "applicationReadOnly": False,
    }


async def attest_service_workload(
    runner: CommandRunner, connection: ServiceConnection, name: str, image: str
) -> dict:
    fmt = '{"labels":{{json .Config.Labels}},"networks":{{json .NetworkSettings.Networks}},"image":{{json .Image}},"imageRef":{{json .Config.Image}},"host":{{json .HostConfig}}}'
    job = await _json(
        runner,
        ("container", "inspect", "--format", fmt, name),
        "service workload attachment is unavailable",
    )
    labels, host = job.get("labels") or {}, job.get("host") or {}
    if (
        set(job.get("networks") or {}) != {connection.network_ref}
        or job.get("imageRef") != image
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(job.get("image", "")))
        or (image.startswith("sha256:") and job.get("image") != image)
        or labels.get(SERVICE_LABEL) != connection.service_ref
        or labels.get(SERVICE_DIGEST_LABEL) != connection.digest
        or host.get("Dns") != ["127.0.0.1"]
        or host.get("ExtraHosts")
        != [f"{connection.server_name}:{connection.relay.listen_host}"]
        or host.get("Privileged") is not False
        or host.get("CapAdd")
        or "ALL" not in (host.get("CapDrop") or [])
    ):
        raise RuntimeError("service workload attachment or authority is mismatched")
    return {
        "serviceRef": connection.service_ref,
        "policyDigest": connection.digest,
        "attachmentIdentity": name,
        "workloadImageDigest": job["image"],
        "validationResult": "passed",
    }
