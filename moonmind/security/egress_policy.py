"""Pure admitted-egress contracts and explicit HTTP/browser adapter options.

Importing admission or tool options does not read deployment proxy files or
require a reachable enforcer; offline jobs keep their independent contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EGRESS_POLICIES_ENV_KEY = "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES"

_REF = r"^[a-z0-9][a-z0-9.-]{0,127}$"

_DOCKER_NAME = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"

_HOST = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$"
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class _PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class ScopedDestination(_PolicyModel):
    host: str
    purpose: Literal["tool-data", "provider-api", "registry-api", "live-target"]
    protocol: Literal["http", "https"]
    port: int = Field(ge=1, le=65535, strict=True)
    target_network_ref: str | None = Field(
        None, alias="targetNetworkRef", pattern=_DOCKER_NAME
    )
    target_container_ref: str | None = Field(
        None, alias="targetContainerRef", pattern=_DOCKER_NAME
    )

    @field_validator("host")
    @classmethod
    def exact_dns_name(cls, value: str) -> str:
        name = value.lower().rstrip(".")
        if not _HOST.fullmatch(name) or name.endswith(
            (".localhost", ".local", ".internal", ".arpa")
        ):
            raise ValueError("egress host must be an exact DNS name")
        return name

    @model_validator(mode="after")
    def private_target(self):
        if (self.target_network_ref is None) != (self.target_container_ref is None):
            raise ValueError(
                "private egress needs both target network and container authority"
            )
        if self.target_network_ref in {"host", "none", "bridge"}:
            raise ValueError("private egress cannot select a built-in network")
        if self.target_network_ref and self.purpose != "live-target":
            raise ValueError("private attachments are for admitted live targets")
        return self


class ScopedEgressPolicy(_PolicyModel):
    policy_ref: str = Field(alias="policyRef", pattern=_REF)
    allowed_workflow_ids: tuple[str, ...] = Field(
        alias="allowedWorkflowIds", min_length=1, max_length=64
    )
    allowed_image_source_refs: tuple[str, ...] = Field(
        alias="allowedImageSourceRefs", min_length=1, max_length=32
    )
    destinations: tuple[ScopedDestination, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unambiguous_authority(self):
        if any(not v.strip() or len(v) > 255 for v in self.allowed_workflow_ids):
            raise ValueError("egress workflow grants must be explicit")
        if any(not re.fullmatch(_REF, v) for v in self.allowed_image_source_refs):
            raise ValueError("egress image grants must be explicit")
        seen = {}
        for d in self.destinations:
            target = (d.target_network_ref, d.target_container_ref)
            if d.host in seen and seen[d.host] != target:
                raise ValueError("egress hostname has conflicting target authority")
            seen[d.host] = target
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(by_alias=True, mode="json"))


def parse_egress_policies(raw: str | None) -> tuple[ScopedEgressPolicy, ...]:
    if not raw or not raw.strip():
        return ()
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("bounded policy array required")
        policies = tuple(ScopedEgressPolicy.model_validate(row) for row in rows)
        if len({p.policy_ref for p in policies}) != len(policies):
            raise ValueError("duplicate policy reference")
        return policies
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "deployment egress policy is invalid; only HTTP(S) is supported"
        ) from exc


def tool_proxy_options(
    adapter: Literal["httpx", "requests", "chromium"],
    *,
    environ: Mapping[str, str],
    protocol: str = "https",
) -> dict:
    """Explicit adapter configuration; clients that ignore env stay confined."""
    if protocol not in {"http", "https"}:
        raise ValueError("unsupported egress protocol; coverage is limited to HTTP(S)")
    proxy = environ.get("MOONMIND_EGRESS_PROXY", "")
    if not re.fullmatch(r"http://[a-z0-9.-]+:3128", proxy):
        raise ValueError("admitted egress proxy is unavailable")
    if adapter == "httpx":
        return {"proxy": proxy, "trust_env": False}
    if adapter == "requests":
        return {"proxies": {"http": proxy, "https": proxy}}
    if adapter == "chromium":
        return {
            "args": [
                f"--proxy-server={proxy}",
                "--proxy-bypass-list=<-loopback>",
                "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ]
        }
    raise ValueError("unsupported egress tool adapter")
