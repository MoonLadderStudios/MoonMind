"""Deployment-owned restricted-egress gateway reconciliation.

The reconciler is intentionally a Docker-bound authority, not a workflow
contract.  It creates an internal workload network whose only egress path is a
dual-homed Squid gateway and authenticates the exact gateway/rule state for the
trusted workload backend.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from moonmind.security.egress_profiles import (
    ATTESTATION_LABELS,
    ENFORCER_IMPLEMENTATION,
    EgressProfile,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]

OWNED_LABEL = "moonmind.egress.reconciler_owned"
DEFAULT_GATEWAY_IMAGE = "ubuntu/squid:latest"


def _acl_name(index: int) -> str:
    return f"mm_destination_{index}"


def compile_squid_policy(profile: EgressProfile) -> tuple[str, str]:
    """Compile data-only profile input to a deterministic, fail-closed policy."""

    lines = [
        "http_port 3128",
        "visible_hostname moonmind-egress-gateway",
        "via off",
        "forwarded_for delete",
        "request_header_access Proxy-Authorization deny all",
        "acl CONNECT method CONNECT",
        "acl allowed_methods method GET HEAD POST PUT PATCH DELETE OPTIONS CONNECT",
        (
            "acl forbidden_v4 dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 "
            "127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 "
            "192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 "
            "203.0.113.0/24 224.0.0.0/4 240.0.0.0/4"
        ),
        "acl forbidden_v6 dst ipv6",
        "http_access deny !allowed_methods",
        "http_access deny forbidden_v4",
        "http_access deny forbidden_v6",
    ]
    for server in profile.dns_servers:
        # EgressProfile validation guarantees these are literal global IPs.
        ipaddress.ip_address(server)
    lines.append("dns_nameservers " + " ".join(profile.dns_servers))
    if profile.resolution_mode == "continuous":
        lines.extend(("positive_dns_ttl 1 seconds", "negative_dns_ttl 1 seconds"))
    for index, destination in enumerate(profile.allowed_destinations):
        acl = _acl_name(index)
        ports = " ".join(str(port) for port in sorted(set(destination.ports)))
        lines.append(f"acl {acl}_ports port {ports}")
        if destination.dns_name:
            # A leading dot authorizes the exact name and its subdomains. The
            # resolved-address deny ACLs are evaluated for every connection,
            # including re-resolution, CNAME chains, and mixed answers.
            lines.append(f"acl {acl}_target dstdomain {destination.dns_name}")
        else:
            lines.append(f"acl {acl}_target dst {destination.cidr}")
        if destination.protocol != "tcp":
            raise ValueError("the HTTP gateway cannot enforce non-TCP destinations")
        lines.append(f"http_access allow {acl}_target {acl}_ports")
    lines.extend(
        (
            "http_access deny all",
            f"client_ip_max_connections {profile.max_connections}",
            f"reply_body_max_size {profile.max_bytes} allow all",
            f"request_body_max_size {profile.max_bytes} allow all",
            f"request_timeout {profile.idle_seconds} seconds",
            "access_log stdio:/var/log/squid/access.log squid",
            "cache_log /var/log/squid/cache.log",
            "cache deny all",
        )
    )
    rendered = "\n".join(lines) + "\n"
    return rendered, "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()


@dataclass(frozen=True)
class ReconciledGateway:
    network_ref: str
    gateway_ref: str
    rules_digest: str
    validated_at: str


class DockerEgressGatewayReconciler:
    """Own one local-Docker gateway/network pair for an immutable profile."""

    def __init__(
        self,
        *,
        runner: CommandRunner,
        state_root: str | Path,
        backend_ref: str,
        attestation_key: bytes,
        gateway_image: str = DEFAULT_GATEWAY_IMAGE,
    ) -> None:
        self._runner = runner
        self._state_root = Path(state_root).resolve()
        self._backend_ref = backend_ref
        self._key = attestation_key
        self._image = gateway_image

    async def _checked(self, args: Sequence[str]) -> bytes:
        code, stdout, stderr = await self._runner(args)
        if code:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise RuntimeError(f"egress reconciliation failed at {args[0]}: {detail}")
        return stdout

    async def reconcile(self, *, profile: EgressProfile, network_ref: str) -> ReconciledGateway:
        gateway_ref = f"{network_ref}-gateway"
        config, rules_digest = compile_squid_policy(profile)
        self._state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_path = self._state_root / f"{profile.profile_id}-{profile.version}.conf"
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(0o600)
        encoded_config = base64.b64encode(config.encode()).decode()

        # Rotate only the owned gateway. Compose may pre-create the workload
        # network for static consumers; it is adopted only after observing the
        # daemon's internal-network flag, so adoption cannot introduce a route.
        code, stdout, _ = await self._runner(
            (
                "container", "inspect", "--format",
                f"{{{{index .Config.Labels \"{OWNED_LABEL}\"}}}}", gateway_ref,
            )
        )
        if code == 0:
            if stdout.decode(errors="replace").strip() != "true":
                raise RuntimeError(
                    f"refusing to replace unowned egress container {gateway_ref!r}"
                )
            await self._checked(("container", "rm", "-f", gateway_ref))

        created_network = False
        code, stdout, _ = await self._runner(
            ("network", "inspect", "--format", "{{.Internal}}", network_ref)
        )
        if code == 0:
            if stdout.decode(errors="replace").strip() != "true":
                raise RuntimeError("restricted-egress workload network is not internal")
        else:
            created_network = True

        validated_at = datetime.now(UTC).isoformat()
        signed_fields = (
            profile.ref,
            profile.digest,
            rules_digest,
            ENFORCER_IMPLEMENTATION,
            validated_at,
            gateway_ref,
            network_ref,
            self._backend_ref,
        )
        labels = {
            OWNED_LABEL: "true",
        }
        if len(self._key) >= 32:
            signature = hmac.new(
                self._key, "\n".join(signed_fields).encode(), hashlib.sha256
            ).hexdigest()
            labels.update(
                {
                    ATTESTATION_LABELS["profile_ref"]: profile.ref,
                    ATTESTATION_LABELS["profile_digest"]: profile.digest,
                    ATTESTATION_LABELS["rules_digest"]: rules_digest,
                    ATTESTATION_LABELS["enforcer"]: ENFORCER_IMPLEMENTATION,
                    ATTESTATION_LABELS["validated"]: "true",
                    ATTESTATION_LABELS["validated_at"]: validated_at,
                    ATTESTATION_LABELS["gateway_ref"]: gateway_ref,
                    ATTESTATION_LABELS["signature"]: signature,
                }
            )
        if created_network:
            network_args = ["network", "create", "--internal"]
            for key, value in labels.items():
                network_args.extend(("--label", f"{key}={value}"))
            network_args.append(network_ref)
            await self._checked(tuple(network_args))
        try:
            await self._checked(
                (
                    "container", "create", "--name", gateway_ref,
                    "--label", f"{OWNED_LABEL}=true",
                    "--label", f"moonmind.egress.rules_digest={rules_digest}",
                    "--network", "bridge", "--cap-drop", "ALL",
                    "--cap-add", "SETUID", "--cap-add", "SETGID",
                    "--security-opt", "no-new-privileges=true",
                    "--read-only",
                    "--tmpfs", "/var/log/squid:rw,nosuid,nodev,noexec",
                    "--tmpfs", "/run:rw,nosuid,nodev,noexec",
                    "--tmpfs", "/var/spool/squid:rw,nosuid,nodev,noexec",
                    "--tmpfs", "/etc/squid:rw,nosuid,nodev,noexec",
                    "--env", f"MOONMIND_SQUID_CONFIG={encoded_config}",
                    "--entrypoint", "/bin/sh",
                    self._image,
                    "-c",
                    (
                        "printf '%s' \"$MOONMIND_SQUID_CONFIG\" | base64 -d > "
                        "/etc/squid/squid.conf && exec squid -N -f "
                        "/etc/squid/squid.conf"
                    ),
                )
            )
            await self._checked(("network", "connect", network_ref, gateway_ref))
            await self._checked(("container", "start", gateway_ref))
            await self._checked(
                (
                    "container", "exec", gateway_ref, "squid", "-k", "parse",
                    "-f", "/etc/squid/squid.conf",
                )
            )
            running = await self._checked(
                (
                    "container", "inspect", "--format", "{{.State.Running}}",
                    gateway_ref,
                )
            )
            if running.decode().strip() != "true":
                raise RuntimeError("egress gateway did not become ready")
        except Exception:
            await self._runner(("container", "rm", "-f", gateway_ref))
            if created_network:
                await self._runner(("network", "rm", network_ref))
            raise
        return ReconciledGateway(network_ref, gateway_ref, rules_digest, validated_at)
