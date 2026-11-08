"""Pure, bounded native TCP authority. DNS is resolved only by lease admission."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_NATIVE_ADDRESSES = 256
MAX_NATIVE_ENDPOINTS = 1024
_DNS = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$"
)
_DOCKER = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"


def canonical_host(value: str) -> str:
    """Accept exact DNS or canonical IPv4 networks, never resolver shorthand."""
    if not isinstance(value, str):
        raise ValueError("native targets must be strings")  # noqa: TRY004 - Pydantic validation error
    try:
        network = ipaddress.IPv4Network(value, strict=True)
    except ValueError:
        name = value.lower().rstrip(".")
        if not _DNS.fullmatch(name) or name.endswith(
            (".localhost", ".local", ".internal", ".arpa", ".svc")
        ):
            raise ValueError("native targets require exact DNS names or canonical IPv4")
        return name
    expected = str(network.network_address) if "/" not in value else str(network)
    if value != expected or network.num_addresses > MAX_NATIVE_ADDRESSES:
        raise ValueError("native address set is noncanonical or exceeds address bound")
    # Private addresses are accepted only with a labeled route below. These
    # special-use routes can never become target authority, even privately.
    prohibited = (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "100.64.0.0/10",
        "168.63.129.16/32",
    )
    if any(network.overlaps(ipaddress.IPv4Network(item)) for item in prohibited):
        raise ValueError("native targets cannot reach special-use control routes")
    return expected


def _expanded(host: str) -> tuple[str, ...]:
    if "/" in host:
        return tuple(str(address) for address in ipaddress.IPv4Network(host))
    return (host,)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class NativeNetworkTarget(_Model):
    hosts: tuple[str, ...] = Field(min_length=1, max_length=MAX_NATIVE_ADDRESSES)
    ports: tuple[int, ...] = Field(min_length=1, max_length=64)
    exclusions: tuple[str, ...] = Field((), max_length=MAX_NATIVE_ADDRESSES)
    purpose: Literal["live-target"]
    target_network_ref: str | None = Field(
        None, alias="targetNetworkRef", pattern=_DOCKER
    )
    target_container_ref: str | None = Field(
        None, alias="targetContainerRef", pattern=_DOCKER
    )

    @field_validator("hosts", "exclusions")
    @classmethod
    def normalize_hosts(cls, values):
        return tuple(sorted({canonical_host(value) for value in values}))

    @field_validator("ports", mode="before")
    @classmethod
    def normalize_ports(cls, values):
        if any(type(value) is not int or not 1 <= value <= 65535 for value in values):
            raise ValueError("native ports must be bounded integers")
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def bounded(self):
        count = sum(
            ipaddress.IPv4Network(h).num_addresses if "/" in h else 1
            for h in self.hosts
        )
        exclusions = sum(
            ipaddress.IPv4Network(h).num_addresses if "/" in h else 1
            for h in self.exclusions
        )
        if exclusions > MAX_NATIVE_ADDRESSES:
            raise ValueError("native exclusion expansion exceeds address bound")
        if (
            count > MAX_NATIVE_ADDRESSES
            or count * len(self.ports) > MAX_NATIVE_ENDPOINTS
        ):
            raise ValueError("native address or address-port product exceeds bound")
        if (self.target_network_ref is None) != (self.target_container_ref is None):
            raise ValueError(
                "private native targets require network and container authority"
            )
        if self.target_network_ref in {"host", "none", "bridge"}:
            raise ValueError("native targets cannot select built-in routes")
        for host in self.hosts:
            try:
                network = ipaddress.IPv4Network(host)
            except ValueError:
                continue
            if not network.is_global and self.target_network_ref is None:
                raise ValueError(
                    "private native addresses require labeled container authority"
                )
        if not self.endpoints():
            raise ValueError("native exclusions remove every endpoint")
        return self

    def endpoints(self) -> tuple[tuple[str, int], ...]:
        """Bounded endpoint expansion; exclusions remove exact names/IPv4 sets."""
        excluded = {host for item in self.exclusions for host in _expanded(item)}
        hosts = {host for item in self.hosts for host in _expanded(item)} - excluded
        return tuple((host, port) for host in sorted(hosts) for port in self.ports)


class NativeNetworkLimits(_Model):
    max_connections: int = Field(alias="maxConnections", strict=True, ge=1, le=1000000)
    max_bytes: int = Field(alias="maxBytes", strict=True, ge=1, le=1099511627776)
    max_connections_per_second: int = Field(
        alias="maxConnectionsPerSecond", strict=True, ge=1, le=1000
    )
    max_concurrent_connections: int = Field(
        alias="maxConcurrentConnections", strict=True, ge=1, le=256
    )
    max_duration_seconds: int = Field(
        alias="maxDurationSeconds", strict=True, ge=1, le=86400
    )


class NativeNetworkSelection(_Model):
    operation: Literal["tcp-connect"] = "tcp-connect"
    targets: tuple[NativeNetworkTarget, ...] = Field(min_length=1, max_length=32)
    limits: NativeNetworkLimits

    @model_validator(mode="after")
    def bounded(self):
        if (
            sum(len(target.endpoints()) for target in self.targets)
            > MAX_NATIVE_ENDPOINTS
        ):
            raise ValueError("native selection exceeds endpoint bound")
        identities = {}
        for target in self.targets:
            route = (
                target.purpose,
                target.target_network_ref,
                target.target_container_ref,
            )
            for host, _ in target.endpoints():
                if host in identities and identities[host] != route:
                    raise ValueError("native host has conflicting route authority")
                identities[host] = route
        return self


def require_native_subset(
    requested: NativeNetworkSelection, authority: NativeNetworkSelection | None
) -> None:
    """Validate the exact requested set; never silently narrow or replace it."""
    if authority is None or requested.operation != authority.operation:
        raise ValueError("native operation is not authorized")
    approved = set()
    for target in authority.targets:
        route = (target.purpose, target.target_network_ref, target.target_container_ref)
        approved.update((*endpoint, *route) for endpoint in target.endpoints())
    for target in requested.targets:
        # DNS may later resolve into a numeric exclusion. Preserve every
        # applicable authority exclusion until trusted resolution. Unrelated
        # host groups sharing a route do not constrain a narrowed selection.
        required_exclusions = {
            value
            for allowed in authority.targets
            if (
                allowed.purpose,
                allowed.target_network_ref,
                allowed.target_container_ref,
            )
            == (target.purpose, target.target_network_ref, target.target_container_ref)
            if set(allowed.endpoints()) & set(target.endpoints())
            for item in allowed.exclusions
            for value in _expanded(item)
        }
        supplied_exclusions = {
            value for item in target.exclusions for value in _expanded(item)
        }
        if not required_exclusions <= supplied_exclusions:
            raise ValueError("native selection cannot omit authority exclusions")
        route = (target.purpose, target.target_network_ref, target.target_container_ref)
        if any((*endpoint, *route) not in approved for endpoint in target.endpoints()):
            raise ValueError("native endpoint is outside authority")
    if any(
        getattr(requested.limits, name) > getattr(authority.limits, name)
        for name in type(requested.limits).model_fields
    ):
        raise ValueError("native allocation exceeds authority")
