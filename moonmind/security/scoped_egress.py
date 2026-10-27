"""Deployment-admitted policies for leases of the existing Squid egress owner.

A job selects a reference, never destinations or Docker attachments. DNS is
validated before admission and pinned in Squid's hosts file. An existing proxy
retains that snapshot; loss before a create receipt permits revalidation under
the same policy and any surviving owned network's original deadline.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import socket
from collections.abc import Mapping
from datetime import UTC, datetime

from moonmind.security.egress import (
    _FORBIDDEN_NETWORKS,
    DEFAULT_EGRESS_PROFILE,
    CommandRunner,
    attest_docker_egress,
    bounded_denial_diagnostics,
    denied_connection_count,
)
from moonmind.security.egress_policy import ScopedEgressPolicy, _canonical, _digest
from moonmind.utils.logging import redact_sensitive_text

_CONFIG = "/tmp/moonmind-squid.conf"
_HOSTS = "/tmp/moonmind-hosts"
_SNAPSHOT = "/tmp/moonmind-scope.json"
_LOG = "/tmp/moonmind-access.log"
_LABEL = "moonmind.egress.scope"
_OWNER = "moonmind.ownership"
_DEADLINE = "moonmind.expires_at"
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
    ):
        self.runner, self.policy = runner, policy
        self.ownership, self.job_id, self.workload_ref = ownership, job_id, workload_ref
        self.gateway_ref = (
            "moonmind-egress-" + hashlib.sha256(ownership.encode()).hexdigest()[:20]
        )
        self.network_ref = self.gateway_ref + "-internal"
        self.outbound_ref = self.gateway_ref + "-outbound"

    @property
    def proxy_url(self) -> str:
        return f"http://{self.gateway_ref}:3128"

    def proxy_env(self) -> tuple[str, ...]:
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

    async def _optional(self, name: str, *, network: bool = False) -> dict | None:
        args = ("network", "inspect") if network else ("inspect",)
        code, out, _ = await self.runner((*args, "--format", "{{json .}}", name))
        if code:
            # An unavailable daemon is not evidence of absence. A successful
            # ownership-filtered inventory must independently confirm absence.
            listing = ("network", "ls") if network else ("ps", "-a")
            listing_code, names, _ = await self.runner(
                (*listing, "--format", "{{.Name}}" if network else "{{.Names}}")
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
        destinations = [d for d in self.policy.destinations if d.target_network_ref]
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

    async def prepare(
        self, *, timeout_seconds: int, previously_admitted: bool = False
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
                    for d in self.policy.destinations
                    if d.target_network_ref
                }
                for network in sorted(expected - networks):
                    await self._checked("network", "connect", network, existing["Id"])
                await self._checked("start", existing["Id"])
                return await self._wait_ready()
            return await self.attest()
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
        for d in self.policy.destinations:
            if not d.target_network_ref and d.host not in addresses:
                addresses[d.host] = await _public_answers(d.host, d.port)
        expires = math.floor(datetime.now(UTC).timestamp()) + timeout_seconds
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
                    "--label",
                    f"{_DEADLINE}={expires}",
                ]
                if internal:
                    args.extend(
                        (
                            "--internal",
                            "--opt",
                            "com.docker.network.bridge.inhibit_ipv4=true",
                        )
                    )
                await self._checked(*args, name)
                network = await self._optional(name, network=True)
            self._owned(network or {}, network=True)
            expires = min(expires, float(network["Labels"][_DEADLINE]))
        internal = await self._optional(self.network_ref, network=True)
        subnet = ipaddress.ip_network(internal["IPAM"]["Config"][0]["Subnet"])
        if subnet.version != 4 or subnet.num_addresses < 8:
            raise RuntimeError("scoped egress network address space is invalid")
        proxy_address, client_address = str(subnet[2]), str(subnet[3])
        config, hosts = render_scoped_proxy(
            self.policy,
            addresses,
            listen_address=proxy_address,
            client_address=client_address,
        )
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
                _COMMAND,
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
                    for d in self.policy.destinations
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
            config, hosts = render_scoped_proxy(
                self.policy,
                snapshot["addresses"],
                listen_address=snapshot["proxyAddress"],
                client_address=snapshot["clientAddress"],
            )
            valid_snapshot = (
                snapshot["policyDigest"] == self.policy.digest
                and snapshot["policyRef"] == self.policy.policy_ref
                and gateway["Image"] == snapshot["gatewayImageDigest"] == cfg["Image"]
                and float(snapshot["expiresAt"]) == float(cfg["Labels"][_DEADLINE])
                and float(snapshot["expiresAt"]) > datetime.now(UTC).timestamp()
                and int(env["MOONMIND_EGRESS_DEADLINE"]) == int(snapshot["expiresAt"])
                and base64.b64decode(env["MOONMIND_EGRESS_CONFIG"], validate=True)
                == config
                and base64.b64decode(env["MOONMIND_EGRESS_HOSTS"], validate=True)
                == hosts
            )
            if set(snapshot["addresses"]) != {d.host for d in self.policy.destinations}:
                valid_snapshot = False
            for d in self.policy.destinations:
                if not d.target_network_ref:
                    validate_public_answers(snapshot["addresses"][d.host])
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "scoped egress proxy policy is unavailable or tampered"
            ) from exc
        expected_networks = {self.network_ref, self.outbound_ref} | {
            d.target_network_ref
            for d in self.policy.destinations
            if d.target_network_ref
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
            or cfg.get("Cmd") != ["-c", _COMMAND]
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
            or any(m.get("Destination") != "/tmp" for m in gateway.get("Mounts", []))
        ):
            raise RuntimeError(
                "scoped egress proxy launch, policy, or attachments are unattested"
            )
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
                ("exec", gateway["Id"], "squid", "-k", "check", "-f", _CONFIG)
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
        log = await self._checked("exec", gateway["Id"], "cat", _LOG)
        denied = denied_connection_count(
            log,
            client_address=snapshot["clientAddress"],
            started_at=started_at,
            finished_at=finished_at,
        )
        return {
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
            "purposes": sorted({d.purpose for d in self.policy.destinations}),
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
        for name in (self.network_ref, self.outbound_ref):
            network = await self._optional(name, network=True)
            if network is not None:
                self._owned(network, network=True, cleanup=True)
                # Docker refuses removal if any live endpoint remains. In
                # particular, never disconnect or remove an unowned peer.
                await self._checked("network", "rm", network["Id"])
