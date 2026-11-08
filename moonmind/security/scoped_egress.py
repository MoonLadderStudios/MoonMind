"""Deployment-admitted HTTP and bounded native TCP leases of the egress owner.

Jobs select deployment-admitted authority, never Docker attachments. DNS is
validated once and pinned in the gateway snapshot. Native retries require the
same live gateway and durable counters; retained state never grants a reset.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import math
import socket
import tarfile
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace

from moonmind.security.egress import (
    _FORBIDDEN_NETWORKS,
    DEFAULT_EGRESS_PROFILE,
    CommandRunner,
    attest_docker_egress,
    bounded_denial_diagnostics,
    denied_connection_count,
)
from moonmind.security.egress_conformance_evidence import verify_evidence_digest
from moonmind.security.egress_policy import ScopedEgressPolicy, _canonical, _digest
from moonmind.security.native_network import (
    NativeNetworkSelection,
    require_native_subset,
    require_native_transport_bytes,
)
from moonmind.security.native_tcp_gateway import NativeTCPConfig
from moonmind.utils.logging import redact_sensitive_text

_CONFIG = "/tmp/moonmind-squid.conf"
_HOSTS = "/tmp/moonmind-hosts"
_SNAPSHOT = "/tmp/moonmind-scope.json"
_LOG = "/tmp/moonmind-access.log"
_LABEL = "moonmind.egress.scope"
_OWNER = "moonmind.ownership"
_DEADLINE = "moonmind.expires_at"
_WEBSOCKET_UPGRADE = "http_upgrade_request_protocols websocket allow all"
# All input here is base64-encoded declarative data, not shell text. The installed
# gateway image supplies Squid; source revisions do not select a different image.
_COMMAND = (
    "set -eu; umask 077; "
    f'printf %s "$MOONMIND_EGRESS_CONFIG" | base64 -d > {_CONFIG}; '
    f'printf %s "$MOONMIND_EGRESS_HOSTS" | base64 -d > {_HOSTS}; '
    f'printf %s "$MOONMIND_EGRESS_SNAPSHOT" | base64 -d > {_SNAPSHOT}; '
    f"chmod 0444 {_CONFIG} {_HOSTS} {_SNAPSHOT}; "
    "remaining=$((MOONMIND_EGRESS_DEADLINE - $(date +%s))); "
    '[ "$remaining" -gt 0 ]; '
    f'exec timeout --kill-after=5s "$remaining" squid -NYC -d 1 -f {_CONFIG}'
)


_NATIVE_STATE = "/var/lib/moonmind-native-egress"
_NATIVE_SCRIPT = "/app/moonmind/security/native_tcp_gateway.py"
_NATIVE_COMMAND = (
    "set -eu; umask 077; "
    f'printf %s "$MOONMIND_EGRESS_CONFIG" | base64 -d > {_CONFIG}; '
    f'printf %s "$MOONMIND_EGRESS_HOSTS" | base64 -d > {_HOSTS}; '
    f'printf %s "$MOONMIND_EGRESS_SNAPSHOT" | base64 -d > {_SNAPSHOT}; '
    f"chmod 0444 {_CONFIG} {_HOSTS} {_SNAPSHOT}; "
    f"exec python {_NATIVE_SCRIPT} --config {_CONFIG} --state-directory {_NATIVE_STATE}"
)


async def _json(runner: CommandRunner, *args: str) -> dict:
    code, stdout, _ = await runner(args)
    try:
        value = json.loads(stdout) if not code else None
        if not isinstance(value, dict):
            raise TypeError("object required")
        return value
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "scoped egress authority is unavailable or malformed"
        ) from exc


def validate_public_answers(values) -> tuple[str, ...]:
    addresses = tuple(sorted({str(ipaddress.ip_address(v)) for v in values}))
    if (
        not addresses
        or len(addresses) > 32
        or any(
            not ipaddress.ip_address(v).is_global
            or any(
                ipaddress.ip_address(v) in network for network in _FORBIDDEN_NETWORKS
            )
            for v in addresses
        )
    ):
        raise RuntimeError("scoped egress DNS includes an unapproved address")
    ipv4 = tuple(v for v in addresses if ipaddress.ip_address(v).version == 4)
    if not ipv4:
        raise RuntimeError("scoped egress requires an approved IPv4 destination")
    return ipv4


async def _public_answers(host: str, port: int) -> tuple[str, ...]:
    # Include IPv6 in the inspection: discarding an unsafe answer must never
    # convert a mixed answer set into an admitted destination.
    records = await asyncio.wait_for(
        asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM),
        timeout=10,
    )
    return validate_public_answers(row[4][0] for row in records)


def render_scoped_proxy(
    policy: ScopedEgressPolicy,
    addresses: Mapping[str, tuple[str, ...]],
    *,
    listen_address: str = "0.0.0.0",
    client_address: str | None = None,
) -> tuple[bytes, bytes]:
    """Exact host/method/port grants with pinned DNS and no default exceptions."""
    hosts = "".join(
        f"{address} {host}\n"
        for host in sorted(addresses)
        for address in addresses[host]
    )
    lines = [
        f"http_port {listen_address}:3128",
        "visible_hostname moonmind-scoped-egress",
        f"hosts_file {_HOSTS}",
        "dns_nameservers 127.0.0.1",
        "pid_filename /tmp/moonmind-squid.pid",
        f"access_log stdio:{_LOG}",
        "cache_log /tmp/moonmind-cache.log",
        "cache_store_log none",
        "cache deny all",
        # Squid strips Upgrade by default. Permit only the WebSocket token;
        # the unchanged http_access rules still gate every host, port and client.
        _WEBSOCKET_UPGRADE,
        "pinger_enable off",
        "shutdown_lifetime 0 seconds",
        "request_timeout 30 seconds",
        "read_timeout 300 seconds",
        "acl connection_limit maxconn 128",
        "http_access deny connection_limit",
        "acl tunnel method CONNECT",
        "acl web proto HTTP",
    ]
    if client_address:
        lines.extend(
            [
                f"acl scope_client src {client_address}/32",
                "http_access deny !scope_client",
            ]
        )
    for index, d in enumerate(policy.destinations):
        name = f"scope_{index}"
        lines.extend(
            [
                f"acl {name}_host dstdomain -n {d.host}",
                f"acl {name}_port port {d.port}",
                f"acl {name}_addresses dst {' '.join(addresses[d.host])}",
                f"http_access allow {name}_host {name}_port {name}_addresses "
                + ("tunnel" if d.protocol == "https" else "!tunnel web"),
            ]
        )
    lines.append("http_access deny all")
    return ("\n".join(lines) + "\n").encode(), hosts.encode()


class ScopedEgressLease:
    """One job-owned lease of the installed proxy, recovered from Docker state.

    The job never joins a target or outbound network. Only its trusted proxy
    does. Internal isolated bridges have no host interface, and the proxy has
    IP forwarding disabled, so changing client/proxy settings cannot add a route.
    """

    def __init__(
        self,
        runner: CommandRunner,
        policy: ScopedEgressPolicy | None,
        *,
        ownership: str,
        job_id: str,
        workload_ref: str,
        native_selection: NativeNetworkSelection | None = None,
    ):
        self.runner, self.policy = runner, policy
        self.native_selection = native_selection
        if native_selection is not None and policy is not None:
            require_native_subset(native_selection, policy.native_network)
        self.ownership, self.job_id, self.workload_ref = ownership, job_id, workload_ref
        self.gateway_ref = (
            "moonmind-egress-" + hashlib.sha256(ownership.encode()).hexdigest()[:20]
        )
        self.network_ref = self.gateway_ref + "-internal"
        self.subnet_ref = self.network_ref + "-subnet"
        self.outbound_ref = self.gateway_ref + "-outbound"
        self.state_ref = self.gateway_ref + "-native-state"

    @property
    def proxy_url(self) -> str:
        return f"http://{self.gateway_ref}:3128"

    @property
    def _destinations(self):
        if self.native_selection is None:
            return self.policy.destinations
        return tuple(
            SimpleNamespace(
                host=host,
                port=port,
                purpose=target.purpose,
                target_network_ref=target.target_network_ref,
                target_container_ref=target.target_container_ref,
            )
            for target in self.native_selection.targets
            for host, port in target.endpoints()
        )

    def proxy_env(self) -> tuple[str, ...]:
        if self.native_selection is not None:
            return tuple(
                f"{key}="
                for key in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "ALL_PROXY",
                    "all_proxy",
                    "NO_PROXY",
                    "no_proxy",
                )
            ) + (
                f"MOONMIND_EGRESS_PROXY={self.proxy_url}",
                f"MOONMIND_EGRESS_POLICY_REF={self.policy.policy_ref}",
            )
        return tuple(
            f"{key}={self.proxy_url}"
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "http_proxy",
                "https_proxy",
                "MOONMIND_EGRESS_PROXY",
            )
        ) + (
            "ALL_PROXY=",
            "all_proxy=",
            "NO_PROXY=",
            "no_proxy=",
            f"MOONMIND_EGRESS_POLICY_REF={self.policy.policy_ref}",
        )

    async def _checked(self, *args: str) -> bytes:
        code, out, stderr = await self.runner(args)
        if code:
            detail = redact_sensitive_text(stderr.decode(errors="replace").strip())[
                :512
            ]
            raise RuntimeError(
                f"scoped egress {args[0]} failed (exit code {code}): {detail}"
            )
        return out

    async def _optional(
        self, name: str, *, network: bool = False, volume: bool = False
    ) -> dict | None:
        args = (
            ("volume", "inspect")
            if volume
            else ("network", "inspect")
            if network
            else ("inspect",)
        )
        code, out, _ = await self.runner((*args, "--format", "{{json .}}", name))
        if code:
            # An unavailable daemon is not evidence of absence. A successful
            # ownership-filtered inventory must independently confirm absence.
            listing = (
                ("volume", "ls")
                if volume
                else ("network", "ls")
                if network
                else ("ps", "-a")
            )
            listing_code, names, _ = await self.runner(
                (
                    *listing,
                    "--format",
                    "{{.Name}}" if network or volume else "{{.Names}}",
                )
            )
            if listing_code or name in names.decode().splitlines():
                raise RuntimeError("scoped egress resource identity is unavailable")
            return None
        try:
            value = json.loads(out)
            if not isinstance(value, dict):
                raise TypeError("object required")
            return value
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise RuntimeError("scoped egress resource identity is malformed") from exc

    def _owned(
        self, value: dict, *, network: bool = False, cleanup: bool = False
    ) -> None:
        labels = (
            value.get("Labels")
            if network
            else (value.get("Config") or {}).get("Labels")
        )
        if (
            not isinstance(labels, dict)
            or labels.get(_OWNER) != self.ownership
            or (not cleanup and labels.get(_LABEL) != self.policy.digest)
        ):
            raise RuntimeError("scoped egress ownership or policy mismatch")

    async def _private_addresses(
        self, *, gateway_id: str | None = None
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
        destinations = [d for d in self._destinations if d.target_network_ref]
        addresses, identities = {}, {}
        network_targets = {}
        for d in destinations:
            target = await _json(
                self.runner, "inspect", "--format", "{{json .}}", d.target_container_ref
            )
            networks = (target.get("NetworkSettings") or {}).get("Networks") or {}
            labels = (target.get("Config") or {}).get("Labels") or {}
            if (
                set(networks) != {d.target_network_ref}
                or labels.get("moonmind.security.target") != self.policy.policy_ref
                or (target.get("HostConfig") or {}).get("NetworkMode") == "host"
            ):
                raise RuntimeError(
                    "private egress target is not an isolated admitted assessment target"
                )
            address = ipaddress.ip_address(
                (networks[d.target_network_ref] or {}).get("IPAddress", "")
            )
            if (
                address.version != 4
                or not address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_unspecified
                or address.is_multicast
            ):
                raise RuntimeError("private egress target address is prohibited")
            target_id = target.get("Id")
            if not target_id:
                raise RuntimeError("private egress target identity is unavailable")
            identities[d.target_container_ref] = target_id
            network_targets.setdefault(d.target_network_ref, set()).add(target_id)
            try:
                numeric = ipaddress.ip_address(d.host)
            except ValueError:
                numeric = None
            if (
                self.native_selection is not None
                and numeric is not None
                and numeric != address
            ):
                raise RuntimeError(
                    "native numeric target differs from inspected container address"
                )
            addresses[d.host] = (str(address),)
        for name, allowed in network_targets.items():
            network = await _json(
                self.runner, "network", "inspect", "--format", "{{json .}}", name
            )
            if (
                network.get("Driver") != "bridge"
                or network.get("Internal") is not True
                or network.get("EnableIPv6") is not False
                or (network.get("Labels") or {}).get("moonmind.security.target")
                != self.policy.policy_ref
                or (network.get("Options") or {}).get(
                    "com.docker.network.bridge.inhibit_ipv4"
                )
                != "true"
                or not allowed <= set(network.get("Containers") or {})
                or set(network.get("Containers") or {})
                - allowed
                - ({gateway_id} if gateway_id else set())
            ):
                raise RuntimeError(
                    "private egress network includes unapproved authority or peers"
                )
        return addresses, identities

    def _validate_native_answers(self, addresses):
        """Validate all answers, retaining the complete immutable DNS snapshot."""
        if set(addresses) != {d.host for d in self._destinations}:
            raise RuntimeError("native resolved authority differs from selection")
        for target in self.native_selection.targets:
            exclusions = []
            for item in target.exclusions:
                try:
                    exclusions.append(ipaddress.IPv4Network(item))
                except ValueError:
                    # Exact excluded hostnames were removed by model expansion.
                    continue
            for host, _ in target.endpoints():
                values = addresses[host]
                if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
                    raise RuntimeError(
                        "native DNS answer set is unavailable or unbounded"
                    )
                try:
                    numeric_host = ipaddress.ip_address(host)
                except ValueError:
                    numeric_host = None
                if numeric_host is not None and tuple(values) != (str(numeric_host),):
                    raise RuntimeError(
                        "native numeric target snapshot differs from selected address"
                    )
                for value in values:
                    if not isinstance(value, str):
                        raise RuntimeError("native DNS address must be a string")  # noqa: TRY004 - attestation failure
                    address = ipaddress.ip_address(value)
                    if address.version != 4:
                        raise RuntimeError(
                            "native TCP requires exclusively IPv4 answers"
                        )
                    if any(address in network for network in exclusions):
                        raise RuntimeError("native DNS address is excluded")
                if not target.target_network_ref:
                    validate_public_answers(values)

    def _native_config(self, snapshot) -> bytes:
        if snapshot.get("nativeSelection") != self.native_selection.model_dump(
            mode="json", by_alias=True
        ):
            raise RuntimeError("native selection differs from admitted snapshot")
        self._validate_native_answers(snapshot["addresses"])
        limits = self.native_selection.limits
        config = NativeTCPConfig.from_mapping(
            {
                "leaseId": self.gateway_ref,
                "deadlineUnix": snapshot["expiresAt"],
                "listenHost": snapshot["proxyAddress"],
                "listenPort": 3128,
                "clientIp": snapshot["clientAddress"],
                "endpoints": [
                    {
                        "authority": f"{host}:{port}",
                        "address": min(snapshot["addresses"][host]),
                        "port": port,
                    }
                    for host, port in sorted(
                        {(d.host, d.port) for d in self._destinations}
                    )
                ],
                "maxConnections": limits.max_connections,
                "maxConcurrentConnections": limits.max_concurrent_connections,
                "maxConnectionsPerSecond": limits.max_connections_per_second,
                "maxBytes": limits.max_bytes,
                "handshakeSeconds": 10,
                "connectSeconds": 10,
                "idleSeconds": 30,
            }
        )
        body = _canonical(config.to_mapping())
        require_native_transport_bytes(body)
        return body

    async def _attest_state_volume(self, expires, *, gateway=None, cleanup=False):
        state = await self._optional(self.state_ref, volume=True)
        self._owned(state or {}, network=True, cleanup=cleanup)
        if (
            state.get("Name") != self.state_ref
            or state.get("Driver") != "local"
            or state.get("Options")
            or state.get("Scope") != "local"
            or float(state["Labels"][_DEADLINE]) != float(expires)
        ):
            raise RuntimeError("native state volume identity is unattested")
        consumers = (
            (
                await self._checked(
                    "ps",
                    "-a",
                    "--no-trunc",
                    "--filter",
                    f"volume={self.state_ref}",
                    "--format",
                    "{{.ID}}",
                )
            )
            .decode()
            .splitlines()
        )
        if set(consumers) != ({gateway["Id"]} if gateway is not None else set()):
            raise RuntimeError(
                "native state volume is not exclusive to the owned gateway"
            )
        if gateway is not None:
            if state["Labels"].get(_LABEL) != gateway["Config"]["Labels"].get(_LABEL):
                raise RuntimeError("native state and gateway policy identities differ")
            mounts = [
                m
                for m in gateway.get("Mounts", [])
                if m.get("Destination") == _NATIVE_STATE
            ]
            if (
                len(mounts) != 1
                or mounts[0].get("Type") != "volume"
                or mounts[0].get("Name") != self.state_ref
                or mounts[0].get("RW") is not True
                or mounts[0].get("Driver") != "local"
            ):
                raise RuntimeError("native state mount is unattested")

    async def _native_counters(self, gateway, snapshot):
        """Read fixed, bounded regular files even after the owned gateway stops."""
        config = NativeTCPConfig.from_mapping(json.loads(self._native_config(snapshot)))
        values = []
        for name in ("startup.json", "snapshot.json"):
            archive = await self._checked(
                "cp", f"{gateway['Id']}:{_NATIVE_STATE}/{name}", "-"
            )
            if len(archive) > 1048576:
                raise RuntimeError("native evidence archive exceeds bound")
            try:
                with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                    members = tar.getmembers()
                    if (
                        len(members) != 1
                        or members[0].name != name
                        or not members[0].isfile()
                        or members[0].size > 524288
                    ):
                        raise ValueError("fixed regular evidence file required")
                    values.append(json.load(tar.extractfile(members[0])))
            except (ValueError, TypeError, tarfile.TarError, UnicodeDecodeError) as exc:
                raise RuntimeError("native evidence archive is malformed") from exc
        startup, counters = values
        if (
            not isinstance(startup, dict)
            or set(startup)
            != {"leaseId", "configDigest", "deadlineUnix", "pid", "processStart"}
            or not isinstance(counters, dict)
            or set(counters)
            != {
                "schemaVersion",
                "leaseId",
                "configDigest",
                "deadlineUnix",
                "pid",
                "processStart",
                "status",
                "attempts",
                "activeConnections",
                "bytesReserved",
                "denials",
                "endpoints",
            }
        ):
            raise RuntimeError("native evidence schema is invalid")
        for value in values:
            if (
                not isinstance(value, dict)
                or value.get("leaseId") != config.lease_id
                or value.get("configDigest") != config.digest
                or value.get("deadlineUnix") != config.deadline_unix
            ):
                raise RuntimeError("native counter evidence identity mismatch")
        if (
            counters.get("schemaVersion") != 1
            or counters.get("status")
            not in {"created", "starting", "running", "closed"}
            or type(startup.get("pid")) is not int
            or startup["pid"] < 1
            or counters.get("pid") != startup["pid"]
            or not isinstance(startup.get("processStart"), str)
            or not startup["processStart"].isdigit()
            or counters.get("processStart") != startup["processStart"]
            or counters.get("endpoints") != config.to_mapping()["endpoints"]
        ):
            raise RuntimeError("native counter evidence process or endpoints mismatch")
        for field, maximum in (
            ("attempts", config.max_connections),
            ("activeConnections", config.max_concurrent_connections),
            ("bytesReserved", config.max_bytes),
        ):
            value = counters.get(field)
            if type(value) is not int or not 0 <= value <= maximum:
                raise RuntimeError("native counter evidence exceeds admitted bounds")
        denials = counters.get("denials")
        if (
            not isinstance(denials, dict)
            or len(denials) > 32
            or any(
                not isinstance(k, str)
                or len(k) > 64
                or type(v) is not int
                or not 0 <= v <= 2**63 - 1
                for k, v in denials.items()
            )
        ):
            raise RuntimeError("native denial evidence is invalid")
        return counters

    def _native_attribution(self, snapshot):
        return {
            "resolvedAddresses": snapshot["addresses"],
            "targetIdentities": snapshot["targetIdentities"],
            "nativeLimits": self.native_selection.limits.model_dump(
                mode="json", by_alias=True
            ),
            "nativeSelectionDigest": _digest(snapshot["nativeSelection"]),
        }

    def validate_final_receipt(self, evidence):
        """Bind already persisted, artifact-owner-verified evidence to this attempt."""
        selection = self.native_selection.model_dump(mode="json", by_alias=True)
        if (
            not isinstance(evidence, dict)
            or evidence.get("gatewayRef") != self.gateway_ref
            or evidence.get("networkRef") != self.network_ref
            or evidence.get("nativeSelectionDigest") != _digest(selection)
            or evidence.get("nativeLimits") != selection["limits"]
            or evidence.get("coverage") == "unverified"
            or not (
                isinstance(evidence.get("nativeNetwork"), dict)
                or isinstance(evidence.get("nativeNoUseWitness"), dict)
            )
        ):
            raise RuntimeError(
                "native final receipt does not match this admitted attempt"
            )
        verify_evidence_digest(evidence)

    async def stop_native(self):
        """Close transport before publishing final counters or removing state."""
        gateway = await self._optional(self.gateway_ref)
        if gateway is not None:
            self._owned(gateway, cleanup=True)
            if (gateway.get("State") or {}).get("Running"):
                await self._checked("stop", "--time", "5", gateway["Id"])

    @staticmethod
    def _never_started(gateway):
        state = gateway.get("State") or {}
        return (
            state.get("Status") == "created"
            and state.get("Running") is False
            and state.get("StartedAt") == "0001-01-01T00:00:00Z"
            and type(gateway.get("RestartCount")) is int
            and gateway["RestartCount"] == 0
        )

    async def _native_no_use_witness(self, gateway, snapshot):
        """Prove no runtime start and an empty state directory, never infer from I/O errors."""
        if not self._never_started(gateway):
            raise RuntimeError("native gateway has no verified never-started identity")
        archive = await self._checked("cp", f"{gateway['Id']}:{_NATIVE_STATE}", "-")
        if len(archive) > 65536 or len(archive) % 512:
            raise RuntimeError(
                "native empty-state archive exceeds bounds or is truncated"
            )
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                members = tar.getmembers()
                if (
                    len(members) != 1
                    or members[0].name
                    not in {"moonmind-native-egress", "moonmind-native-egress/"}
                    or not members[0].isdir()
                    or members[0].size != 0
                    or len(archive) < members[0].offset_data + 1024
                    or any(archive[members[0].offset_data :])
                ):
                    raise ValueError(
                        "state directory is not independently proven empty"
                    )
        except (ValueError, tarfile.TarError) as exc:
            raise RuntimeError(
                "native empty-state evidence is malformed or nonempty"
            ) from exc
        # Reconcile a concurrent start or replacement during the archive read.
        observed = await self._optional(self.gateway_ref)
        if (
            not observed
            or not self._never_started(observed)
            or any(
                observed.get(key) != gateway.get(key)
                for key in ("Id", "Image", "Config", "HostConfig", "Mounts")
            )
        ):
            raise RuntimeError("native gateway changed during no-use observation")
        await self._attest_state_volume(
            snapshot["expiresAt"], gateway=observed, cleanup=True
        )
        return {
            "gatewayIdentity": gateway["Id"],
            "stateDirectoryEmpty": True,
            "stateArchiveDigest": "sha256:" + hashlib.sha256(archive).hexdigest(),
            "status": "created",
            "startedAt": gateway["State"]["StartedAt"],
            "restartCount": 0,
        }

    async def terminal_evidence(self):
        """Retain counters without asserting live runtime health after cancellation."""
        evidence = {
            "coverage": "partial",
            "limitations": ["native_runtime_not_live_attested"],
            "deniedConnectionCount": 0,
            "denialDiagnostics": [],
            "gatewayRef": self.gateway_ref,
            "networkRef": self.network_ref,
        }
        try:
            gateway = await self._optional(self.gateway_ref)
            self._owned(gateway or {}, cleanup=True)
            cfg = gateway["Config"]
            env = dict(item.split("=", 1) for item in cfg["Env"] if "=" in item)
            snapshot = json.loads(
                base64.b64decode(env["MOONMIND_EGRESS_SNAPSHOT"], validate=True)
            )
            if (
                snapshot["policyDigest"] != cfg["Labels"][_LABEL]
                or float(snapshot["expiresAt"]) != float(cfg["Labels"][_DEADLINE])
                or base64.b64decode(env["MOONMIND_EGRESS_CONFIG"], validate=True)
                != self._native_config(snapshot)
                or cfg["Cmd"] != ["-c", _NATIVE_COMMAND]
                or cfg.get("Entrypoint") != ["/bin/sh"]
                or cfg.get("User") != "proxy"
                or gateway.get("Image") != cfg.get("Image")
                or gateway.get("Image") != snapshot["gatewayImageDigest"]
                or int(env["MOONMIND_EGRESS_DEADLINE"]) != int(snapshot["expiresAt"])
                or base64.b64decode(env["MOONMIND_EGRESS_HOSTS"], validate=True) != b""
            ):
                raise RuntimeError("native final evidence configuration is unattested")
            await self._attest_state_volume(
                snapshot["expiresAt"], gateway=gateway, cleanup=True
            )
            if (gateway.get("State") or {}).get("Status") == "created":
                witness = await self._native_no_use_witness(gateway, snapshot)
                evidence.update(
                    nativeNoUseWitness=witness,
                    limitations=["native_gateway_never_started"],
                )
                counters = None
            else:
                counters = await self._native_counters(gateway, snapshot)
                evidence["nativeNetwork"] = counters
            evidence.update(
                **self._native_attribution(snapshot),
                profileRef=snapshot["policyRef"],
                profileDigest=snapshot["policyDigest"],
                snapshotDigest=_digest(snapshot),
                expiresAt=snapshot["expiresAt"],
                deniedConnectionCount=sum(counters["denials"].values())
                if counters
                else 0,
            )
            if evidence["deniedConnectionCount"]:
                evidence["limitations"].append("required_network_scope_denied")
        except Exception as exc:  # noqa: BLE001 - preserve bounded diagnostics without certifying health
            evidence["evidenceError"] = redact_sensitive_text(str(exc))[:512]
            evidence["coverage"] = "unverified"
            evidence["limitations"].append("native_counter_evidence_unavailable")
        return evidence

    async def prepare(
        self,
        *,
        timeout_seconds: int,
        previously_admitted: bool = False,
        deadline: datetime | None = None,
    ) -> dict:
        existing = await self._optional(self.gateway_ref)
        if existing is not None:
            # Never replace an uncertain/running lease or resolve new addresses
            # during an Activity retry. Its admitted snapshot is authoritative.
            if (existing.get("State") or {}).get("Status") == "created":
                await self.attest(_pending=True)
                networks = set(
                    (existing.get("NetworkSettings") or {}).get("Networks") or {}
                )
                expected = {self.network_ref, self.outbound_ref} | {
                    d.target_network_ref
                    for d in self._destinations
                    if d.target_network_ref
                }
                for network in sorted(expected - networks):
                    await self._checked("network", "connect", network, existing["Id"])
                await self._checked("start", existing["Id"])
                return await self._wait_ready()
            return await self.attest()
        if (
            self.native_selection is not None
            and await self._optional(self.state_ref, volume=True) is not None
        ):
            raise RuntimeError(
                "retained native state forbids replacement gateway or DNS admission"
            )
        if previously_admitted:
            raise RuntimeError(
                "saved scoped egress lease is unavailable; refusing new DNS admission"
            )
        installed = await attest_docker_egress(
            runner=self.runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref="container-job/scoped",
        )
        addresses, targets = await self._private_addresses()
        for d in self._destinations:
            if not d.target_network_ref and d.host not in addresses:
                if self.native_selection is None:
                    addresses[d.host] = await _public_answers(d.host, d.port)
                else:
                    try:
                        numeric = ipaddress.ip_address(d.host)
                    except ValueError:
                        records = await asyncio.wait_for(
                            asyncio.to_thread(
                                socket.getaddrinfo,
                                d.host,
                                d.port,
                                type=socket.SOCK_STREAM,
                            ),
                            timeout=10,
                        )
                        addresses[d.host] = tuple(
                            sorted({row[4][0] for row in records})
                        )
                    else:
                        addresses[d.host] = (str(numeric),)
        if self.native_selection is not None:
            self._validate_native_answers(addresses)
            require_native_transport_bytes(
                _canonical(
                    {
                        "nativeSelection": self.native_selection.model_dump(
                            mode="json", by_alias=True
                        ),
                        "addresses": addresses,
                        "targetIdentities": targets,
                    }
                ),
                overhead=1024,
            )
        expires = math.floor(datetime.now(UTC).timestamp()) + timeout_seconds
        if self.native_selection is not None:
            expires = min(
                expires,
                math.floor(datetime.now(UTC).timestamp())
                + self.native_selection.limits.max_duration_seconds,
            )
        if deadline is not None:
            expires = min(expires, math.floor(deadline.timestamp()))
        if expires <= datetime.now(UTC).timestamp():
            raise RuntimeError("scoped egress assessment deadline has expired")
        for name, internal in ((self.network_ref, True), (self.outbound_ref, False)):
            network = await self._optional(name, network=True)
            if network is None:
                args = [
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    "--label",
                    f"{_OWNER}={self.ownership}",
                    "--label",
                    f"{_LABEL}={self.policy.digest}",
                ]
                if internal:
                    args.extend(
                        (
                            "--internal",
                            "--opt",
                            "com.docker.network.bridge.inhibit_ipv4=true",
                        )
                    )
                    # Docker selects a free pool, but --ip requires an explicit
                    # subnet. Release the empty reservation before using that
                    # pool explicitly; a concurrent allocation fails closed.
                    reservation = await self._optional(self.subnet_ref, network=True)
                    if reservation is None:
                        await self._checked(
                            *args, "--label", f"{_DEADLINE}={expires}", self.subnet_ref
                        )
                        reservation = await self._optional(
                            self.subnet_ref, network=True
                        )
                    self._owned(reservation or {}, network=True)
                    subnet = ipaddress.ip_network(
                        reservation["IPAM"]["Config"][0]["Subnet"]
                    )
                    if subnet.version != 4 or subnet.num_addresses < 8:
                        raise RuntimeError(
                            "scoped egress network address space is invalid"
                        )
                    expires = min(expires, float(reservation["Labels"][_DEADLINE]))
                    await self._checked("network", "rm", reservation["Id"])
                    args.extend(("--subnet", str(subnet)))
                await self._checked(*args, "--label", f"{_DEADLINE}={expires}", name)
                network = await self._optional(name, network=True)
            self._owned(network or {}, network=True)
            expires = min(expires, float(network["Labels"][_DEADLINE]))
        internal = await self._optional(self.network_ref, network=True)
        subnet = ipaddress.ip_network(internal["IPAM"]["Config"][0]["Subnet"])
        if subnet.version != 4 or subnet.num_addresses < 8:
            raise RuntimeError("scoped egress network address space is invalid")
        proxy_address, client_address = str(subnet[2]), str(subnet[3])
        snapshot = {
            "policyRef": self.policy.policy_ref,
            "policyDigest": self.policy.digest,
            "addresses": addresses,
            "targetIdentities": targets,
            "gatewayImageDigest": installed.gateway_image_digest,
            "proxyAddress": proxy_address,
            "clientAddress": client_address,
            "expiresAt": expires,
        }
        if self.native_selection is not None:
            snapshot["nativeSelection"] = self.native_selection.model_dump(
                mode="json", by_alias=True
            )
            config, hosts = self._native_config(snapshot), b""
            await self._checked(
                "volume",
                "create",
                "--label",
                f"{_OWNER}={self.ownership}",
                "--label",
                f"{_LABEL}={self.policy.digest}",
                "--label",
                f"{_DEADLINE}={expires}",
                self.state_ref,
            )
            await self._attest_state_volume(expires)
        else:
            config, hosts = render_scoped_proxy(
                self.policy,
                addresses,
                listen_address=proxy_address,
                client_address=client_address,
            )
        seconds = math.ceil(expires - datetime.now(UTC).timestamp())
        if seconds <= 0:
            raise RuntimeError("scoped egress lease has expired")
        args = [
            "create",
            "--name",
            self.gateway_ref,
            "--network",
            self.network_ref,
            "--ip",
            proxy_address,
            "--user",
            "proxy",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--sysctl",
            "net.ipv4.ip_forward=0",
            "--memory",
            "128m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m,mode=1777",
            "--label",
            f"{_OWNER}={self.ownership}",
            "--label",
            f"{_LABEL}={self.policy.digest}",
            "--label",
            f"{_DEADLINE}={expires}",
            "--label",
            f"moonmind.egress.job={self.job_id}",
        ]
        if self.native_selection is not None:
            args.extend(
                (
                    "--mount",
                    f"type=volume,source={self.state_ref},destination={_NATIVE_STATE}",
                )
            )
        if self.native_selection is not None:
            require_native_transport_bytes(_canonical(snapshot))
        for key, body in (
            ("CONFIG", config),
            ("HOSTS", hosts),
            ("SNAPSHOT", _canonical(snapshot)),
        ):
            args.extend(
                ("--env", f"MOONMIND_EGRESS_{key}={base64.b64encode(body).decode()}")
            )
        args.extend(
            (
                "--env",
                f"MOONMIND_EGRESS_DEADLINE={int(expires)}",
                "--entrypoint",
                "/bin/sh",
                installed.gateway_image_digest,
                "-c",
                _NATIVE_COMMAND if self.native_selection is not None else _COMMAND,
            )
        )
        await self._checked(*args)
        # Each connect uses a confirmed owned container id, so a replacement
        # cannot acquire target attachments under a coincidentally reused name.
        gateway = await self._optional(self.gateway_ref)
        if gateway is None:
            raise RuntimeError("saved scoped egress lease is unavailable")
        self._owned(gateway or {})
        gateway_id = gateway["Id"]
        for network in (
            self.outbound_ref,
            *sorted(
                {
                    d.target_network_ref
                    for d in self._destinations
                    if d.target_network_ref
                }
            ),
        ):
            await self._checked("network", "connect", network, gateway_id)
        await self._checked("start", gateway_id)
        return await self._wait_ready()

    async def _wait_ready(self) -> dict:
        # The entrypoint writes its pinned files before Squid starts. Wait only
        # for that bounded readiness, leaving rejected policies visible.
        for attempt in range(10):
            try:
                return await self.attest()
            except RuntimeError:
                if attempt == 9:
                    raise
                await asyncio.sleep(0.2)
        raise RuntimeError("scoped egress proxy did not become ready")

    async def attest(
        self,
        *,
        workload: bool = False,
        expected_image: str | None = None,
        started_at=None,
        finished_at=None,
        _pending: bool = False,
    ) -> dict:
        gateway = await self._optional(self.gateway_ref)
        if gateway is None:
            raise RuntimeError("saved scoped egress lease is unavailable")
        self._owned(gateway or {})
        cfg, host = gateway.get("Config") or {}, gateway.get("HostConfig") or {}
        env = dict(item.split("=", 1) for item in cfg.get("Env", []) if "=" in item)
        try:
            snapshot = json.loads(
                base64.b64decode(env["MOONMIND_EGRESS_SNAPSHOT"], validate=True)
            )
            if self.native_selection is not None:
                config, hosts = self._native_config(snapshot), b""
            else:
                config, hosts = render_scoped_proxy(
                    self.policy,
                    snapshot["addresses"],
                    listen_address=snapshot["proxyAddress"],
                    client_address=snapshot["clientAddress"],
                )
            saved_config = base64.b64decode(
                env["MOONMIND_EGRESS_CONFIG"], validate=True
            )
            # Retain the exact stricter pre-upgrade configuration until those
            # bounded leases expire. Never rewrite a running proxy or broaden
            # its authority; live-file attestation below hashes its saved bytes.
            if self.native_selection is None and saved_config == config.replace(
                (_WEBSOCKET_UPGRADE + "\n").encode(), b"", 1
            ):
                config = saved_config
            valid_snapshot = (
                snapshot["policyDigest"] == self.policy.digest
                and snapshot["policyRef"] == self.policy.policy_ref
                and gateway["Image"] == snapshot["gatewayImageDigest"] == cfg["Image"]
                and float(snapshot["expiresAt"]) == float(cfg["Labels"][_DEADLINE])
                and float(snapshot["expiresAt"]) > datetime.now(UTC).timestamp()
                and int(env["MOONMIND_EGRESS_DEADLINE"]) == int(snapshot["expiresAt"])
                and saved_config == config
                and base64.b64decode(env["MOONMIND_EGRESS_HOSTS"], validate=True)
                == hosts
            )
            if set(snapshot["addresses"]) != {d.host for d in self._destinations}:
                valid_snapshot = False
            for d in self._destinations:
                if not d.target_network_ref:
                    validate_public_answers(snapshot["addresses"][d.host])
            if self.native_selection is not None:
                self._validate_native_answers(snapshot["addresses"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "scoped egress proxy policy is unavailable or tampered"
            ) from exc
        expected_networks = {self.network_ref, self.outbound_ref} | {
            d.target_network_ref for d in self._destinations if d.target_network_ref
        }
        actual_networks = (gateway.get("NetworkSettings") or {}).get("Networks") or {}
        internal_address = (actual_networks.get(self.network_ref) or {}).get(
            "IPAddress"
        ) or (
            (actual_networks.get(self.network_ref) or {}).get("IPAMConfig") or {}
        ).get("IPv4Address")
        if (
            not valid_snapshot
            or cfg.get("Entrypoint") != ["/bin/sh"]
            or cfg.get("Cmd")
            != [
                "-c",
                _NATIVE_COMMAND if self.native_selection is not None else _COMMAND,
            ]
            or cfg.get("User") != "proxy"
            or (not _pending and gateway.get("State", {}).get("Running") is not True)
            or (
                _pending
                and (
                    gateway.get("State", {}).get("Status") != "created"
                    or gateway.get("State", {}).get("Running") is not False
                )
            )
            or host.get("Privileged") is not False
            or host.get("ReadonlyRootfs") is not True
            or host.get("CapAdd")
            or "ALL" not in (host.get("CapDrop") or [])
            or "no-new-privileges" not in (host.get("SecurityOpt") or [])
            or (host.get("Sysctls") or {}).get("net.ipv4.ip_forward") != "0"
            or (
                set(actual_networks) - expected_networks
                if _pending
                else set(actual_networks) != expected_networks
            )
            or internal_address != snapshot["proxyAddress"]
            or set(host.get("Tmpfs") or {}) != {"/tmp"}
            or any(
                m.get("Destination")
                not in (
                    {"/tmp", _NATIVE_STATE}
                    if self.native_selection is not None
                    else {"/tmp"}
                )
                for m in gateway.get("Mounts", [])
            )
        ):
            raise RuntimeError(
                "scoped egress proxy launch, policy, or attachments are unattested"
            )
        if self.native_selection is not None:
            await self._attest_state_volume(snapshot["expiresAt"], gateway=gateway)
        private, identities = await self._private_addresses(gateway_id=gateway["Id"])
        if identities != snapshot["targetIdentities"] or any(
            list(v) != snapshot["addresses"].get(k) for k, v in private.items()
        ):
            raise RuntimeError("scoped egress target identity changed")
        expected_hashes = {
            _CONFIG: hashlib.sha256(config).hexdigest(),
            _HOSTS: hashlib.sha256(hosts).hexdigest(),
            _SNAPSHOT: hashlib.sha256(_canonical(snapshot)).hexdigest(),
        }
        if not _pending:
            observed = (
                await self._checked(
                    "exec", gateway["Id"], "sha256sum", *expected_hashes
                )
            ).decode()
            try:
                observed_hashes = {
                    path: digest
                    for digest, path in (line.split() for line in observed.splitlines())
                }
            except ValueError as exc:
                raise RuntimeError(
                    "scoped egress live policy evidence is malformed"
                ) from exc
            if observed_hashes != expected_hashes:
                raise RuntimeError("scoped egress live policy is tampered")
            forwarding = await self._checked(
                "exec", gateway["Id"], "cat", "/proc/sys/net/ipv4/ip_forward"
            )
            if forwarding.strip() != b"0":
                raise RuntimeError("scoped egress proxy cannot forward IP packets")
            code, _, _ = await self.runner(
                (
                    "exec",
                    gateway["Id"],
                    "python",
                    _NATIVE_SCRIPT,
                    "--config",
                    _CONFIG,
                    "--state-directory",
                    _NATIVE_STATE,
                    "--check",
                )
                if self.native_selection is not None
                else ("exec", gateway["Id"], "squid", "-k", "check", "-f", _CONFIG)
            )
            if code:
                raise RuntimeError("scoped egress proxy process is unavailable")
        allowed_peers = {gateway["Id"]}
        target = None
        if workload:
            target = await _json(
                self.runner, "inspect", "--format", "{{json .}}", self.workload_ref
            )
            labels = (target.get("Config") or {}).get("Labels") or {}
            attachment = (
                (target.get("NetworkSettings") or {}).get("Networks") or {}
            ).get(self.network_ref) or {}
            client_address = attachment.get("IPAddress") or (
                attachment.get("IPAMConfig") or {}
            ).get("IPv4Address")
            if (
                labels.get(_OWNER) != self.ownership
                or labels.get(_LABEL) != self.policy.digest
                or labels.get("moonmind.egress.gateway-id") != gateway["Id"]
                or labels.get("moonmind.egress.snapshot-digest") != _digest(snapshot)
                or (target.get("Config") or {}).get("Image") != expected_image
                or set((target.get("NetworkSettings") or {}).get("Networks") or {})
                != {self.network_ref}
                or (target.get("HostConfig") or {}).get("NetworkMode")
                != self.network_ref
                or client_address != snapshot["clientAddress"]
            ):
                raise RuntimeError(
                    "scoped egress workload identity or attachments changed"
                )
            allowed_peers.add(target["Id"])
        else:
            # Created workload membership is legitimate during create/start
            # Activity reconciliation, but no arbitrary peer may share this net.
            peer = await self._optional(self.workload_ref)
            if peer is not None:
                labels = (peer.get("Config") or {}).get("Labels") or {}
                if labels.get(_OWNER) != self.ownership:
                    raise RuntimeError("scoped egress workload ownership mismatch")
                allowed_peers.add(peer["Id"])
        for name, internal in ((self.network_ref, True), (self.outbound_ref, False)):
            network = await self._optional(name, network=True)
            self._owned(network or {}, network=True)
            if (
                network.get("Driver") != "bridge"
                or network.get("Internal") is not internal
                or network.get("EnableIPv6") is not False
                or (internal and set(network.get("Containers") or {}) - allowed_peers)
                or (
                    internal
                    and (network.get("Options") or {}).get(
                        "com.docker.network.bridge.inhibit_ipv4"
                    )
                    != "true"
                )
            ):
                raise RuntimeError(
                    "scoped egress network has unapproved routes or peers"
                )
        if _pending:
            return {
                "evidenceStage": "created_unstarted",
                "snapshotDigest": _digest(snapshot),
            }
        if self.native_selection is not None:
            counters = await self._native_counters(gateway, snapshot)
            denied = sum(counters["denials"].values())
            log = b""
        else:
            counters = None
            log = await self._checked("exec", gateway["Id"], "cat", _LOG)
        denied = (
            denied
            if counters is not None
            else denied_connection_count(
                log,
                client_address=snapshot["clientAddress"],
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        return {
            **(
                {"nativeNetwork": counters, **self._native_attribution(snapshot)}
                if counters is not None
                else {}
            ),
            "profileRef": self.policy.policy_ref,
            "profileDigest": self.policy.digest,
            "networkRef": self.network_ref,
            "gatewayRef": self.gateway_ref,
            "gatewayIdentity": gateway["Id"],
            "gatewayImageDigest": gateway["Image"],
            "snapshotDigest": _digest(snapshot),
            "proxyAddress": snapshot["proxyAddress"],
            "clientAddress": snapshot["clientAddress"],
            "expiresAt": snapshot["expiresAt"],
            "resolution": "validated_pinned_job_snapshot",
            "purposes": sorted({d.purpose for d in self._destinations}),
            "attachmentIdentity": self.workload_ref if target else None,
            "deniedConnectionCount": denied,
            "denialDiagnostics": list(
                bounded_denial_diagnostics(
                    log,
                    client_address=snapshot["clientAddress"],
                    started_at=started_at,
                    finished_at=finished_at,
                )
            ),
            "coverage": "partial" if denied else "complete" if target else "unverified",
            "limitations": ["required_network_scope_denied"] if denied else [],
        }

    async def cleanup(self) -> None:
        gateway = await self._optional(self.gateway_ref)
        if gateway is not None:
            self._owned(gateway, cleanup=True)
            await self._checked("rm", "--force", gateway["Id"])
        if self.native_selection is not None:
            state = await self._optional(self.state_ref, volume=True)
            if state is not None:
                self._owned(state, network=True, cleanup=True)
                await self._checked("volume", "rm", self.state_ref)
        for name in (self.network_ref, self.outbound_ref, self.subnet_ref):
            network = await self._optional(name, network=True)
            if network is not None:
                self._owned(network, network=True, cleanup=True)
                # Docker refuses removal if any live endpoint remains. In
                # particular, never disconnect or remove an unowned peer.
                await self._checked("network", "rm", network["Id"])
