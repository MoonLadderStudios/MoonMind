"""Pure bounded service-inventory contracts; native TCP admission owns authority.

Plans do not resolve names or open sockets. Peer text is unverified observation,
never a new target, executable instruction, or verified vulnerability. Only the
collector can supply pinned identities from the existing admitted lease.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from moonmind.security.native_network import (
    MAX_NATIVE_ENDPOINTS,
    NativeNetworkSelection,
)
from moonmind.utils.logging import redact_sensitive_text

MAX_SERVICE_INVENTORY_PLAN_BYTES = 96 * 1024
SERVICE_INVENTORY_OUTPUT_DIRECTORY = "/outputs/service-inventory"
_PLAN_CHUNK_CHARS = 4096

MAX_SERVICE_INVENTORY_EXECUTION_BYTES = 8 * 1024 * 1024
MAX_SERVICE_INVENTORY_OUTPUT_BYTES = MAX_SERVICE_INVENTORY_EXECUTION_BYTES
SERVICE_INVENTORY_OUTPUT_FILES = {
    "execution": "execution.json",
    "summary": "summary.json",
}
INTERPRETATION = (
    "TCP reachability and bounded peer observations describe only the admitted "
    "endpoints at assessment time. Endpoint outcomes are runner observations; "
    "retained transport counters provide aggregate evidence. Peer service, "
    "product and version claims are "
    "unverified, not confirmed vulnerabilities. Uncertain or missing results do "
    "not establish absence. No TLS, authentication, redirects or discovered "
    "neighbors are inspected."
)
Probe = Literal["connect", "banner", "http-head"]
Reachability = Literal[
    "open",
    "refused",
    "timed_out",
    "unreachable",
    "denied",
    "untested",
    "transport_error",
]
ProbeStatus = Literal[
    "not_requested",
    "observed",
    "empty",
    "malformed",
    "timed_out",
    "transport_error",
    "untested",
    "budget_exhausted",
    "canceled",
]
JobState = Literal["succeeded", "failed", "canceled", "timed_out", "rejected"]
StopReason = Literal[
    "complete",
    "canceled",
    "duration_exhausted",
    "connection_budget_exhausted",
    "byte_budget_exhausted",
    "transport_unavailable",
    "evidence_bound_exceeded",
    "runner_error",
]


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, alias_generator=to_camel
    )


class ServiceInventoryOptions(_Model):
    """Probe choices specialize native ports, never grant target authority."""

    banner_ports: tuple[int, ...] = Field((), max_length=64)
    http_head_ports: tuple[int, ...] = Field((), max_length=64)
    connect_timeout_seconds: int = Field(5, strict=True, ge=1, le=30)
    probe_timeout_seconds: int = Field(2, strict=True, ge=1, le=10)
    max_banner_bytes: int = Field(1024, strict=True, ge=1, le=4096)

    @field_validator("banner_ports", "http_head_ports", mode="before")
    @classmethod
    def _ports(cls, values):
        if not isinstance(values, (list, tuple)) or any(
            type(port) is not int or not 1 <= port <= 65535 for port in values
        ):
            raise ValueError("probe ports must be bounded integer lists")
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def _disjoint(self):
        if set(self.banner_ports) & set(self.http_head_ports):
            raise ValueError("each port admits at most one optional probe")
        return self


def _key(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ServiceInventoryEndpoint:
    host: str
    port: int
    probe: Probe

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class ServiceInventoryPlan:
    """One immutable expansion of the existing admitted selection."""

    native_network: NativeNetworkSelection
    options: ServiceInventoryOptions
    endpoints: tuple[ServiceInventoryEndpoint, ...] = field(init=False)
    capture_bytes_per_endpoint: int = field(init=False)
    plan_key: str = field(init=False)

    def __post_init__(self):
        endpoints = sorted(
            {
                endpoint
                for target in self.native_network.targets
                for endpoint in target.endpoints()
            }
        )
        ports = {port for _, port in endpoints}
        if (set(self.options.banner_ports) | set(self.options.http_head_ports)) - ports:
            raise ValueError("probe choices must be selected native ports")
        object.__setattr__(
            self,
            "endpoints",
            tuple(
                ServiceInventoryEndpoint(
                    host,
                    port,
                    (
                        "banner"
                        if port in self.options.banner_ports
                        else (
                            "http-head"
                            if port in self.options.http_head_ports
                            else "connect"
                        )
                    ),
                )
                for host, port in endpoints
            ),
        )
        # Each captured byte can expand to six JSON bytes. Reserve bounded
        # record metadata and the execution envelope even at 1,024 endpoints.
        object.__setattr__(
            self,
            "capture_bytes_per_endpoint",
            min(
                self.options.max_banner_bytes,
                (MAX_SERVICE_INVENTORY_EXECUTION_BYTES - 512 * 1024)
                // (6 * len(endpoints)),
            ),
        )
        object.__setattr__(
            self,
            "plan_key",
            _key(
                {
                    "nativeNetwork": self.native_network.model_dump(
                        mode="json", by_alias=True
                    ),
                    "serviceInventory": self.options.model_dump(
                        mode="json", by_alias=True
                    ),
                    "captureBytesPerEndpoint": self.capture_bytes_per_endpoint,
                    "protocol": "service-inventory/v1",
                }
            ),
        )
        _plan_bytes(self)

    def http_head_request(self, endpoint: ServiceInventoryEndpoint) -> bytes:
        if endpoint not in self.endpoints or endpoint.probe != "http-head":
            raise ValueError("fixed HTTP HEAD requires an admitted HTTP probe")
        return (
            f"HEAD / HTTP/1.1\r\nHost: {endpoint.authority}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")


def build_service_inventory_plan(
    native_network: NativeNetworkSelection, options: ServiceInventoryOptions
) -> ServiceInventoryPlan:
    return ServiceInventoryPlan(native_network, options)


def _plan_bytes(plan: ServiceInventoryPlan) -> bytes:
    payload = json.dumps(
        {
            "schemaVersion": "service-inventory-plan/v1",
            "nativeNetwork": plan.native_network.model_dump(mode="json", by_alias=True),
            "serviceInventory": plan.options.model_dump(mode="json", by_alias=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(payload) > MAX_SERVICE_INVENTORY_PLAN_BYTES:
        raise ValueError("service inventory plan exceeds serialized byte bound")
    return payload


def service_inventory_plan_arguments(plan: ServiceInventoryPlan) -> tuple[str, ...]:
    """Fit canonical plan bytes inside existing per-argument container limits."""
    encoded = base64.b64encode(_plan_bytes(plan)).decode("ascii")
    return (
        SERVICE_INVENTORY_OUTPUT_DIRECTORY,
        *(
            encoded[i : i + _PLAN_CHUNK_CHARS]
            for i in range(0, len(encoded), _PLAN_CHUNK_CHARS)
        ),
    )


def build_service_inventory_command(plan: ServiceInventoryPlan) -> list[str]:
    return [
        "-I",
        "-m",
        "moonmind.security.service_inventory_runner",
        *service_inventory_plan_arguments(plan),
    ]


def parse_service_inventory_plan_arguments(
    arguments: list[str] | tuple[str, ...],
) -> ServiceInventoryPlan:
    """Accept only the fixed canonical runner CLI, without flags or paths."""
    max_chunks = (
        4 * ((MAX_SERVICE_INVENTORY_PLAN_BYTES + 2) // 3) + _PLAN_CHUNK_CHARS - 1
    ) // _PLAN_CHUNK_CHARS
    if (
        not isinstance(arguments, (list, tuple))
        or not 2 <= len(arguments) <= max_chunks + 1
        or arguments[0] != SERVICE_INVENTORY_OUTPUT_DIRECTORY
        or any(
            not isinstance(chunk, str) or not 1 <= len(chunk) <= _PLAN_CHUNK_CHARS
            for chunk in arguments[1:]
        )
    ):
        raise ValueError("invalid bounded service inventory plan arguments")
    try:
        raw = base64.b64decode("".join(arguments[1:]), validate=True)
        if len(raw) > MAX_SERVICE_INVENTORY_PLAN_BYTES:
            raise ValueError("service inventory plan exceeds serialized byte bound")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "nativeNetwork", "serviceInventory"}
            or value["schemaVersion"] != "service-inventory-plan/v1"
        ):
            raise ValueError("invalid service inventory plan envelope")
        plan = build_service_inventory_plan(
            NativeNetworkSelection.model_validate(value["nativeNetwork"]),
            ServiceInventoryOptions.model_validate(value["serviceInventory"]),
        )
    except (binascii.Error, UnicodeError, TypeError, KeyError) as exc:
        raise ValueError("invalid service inventory plan encoding") from exc
    if tuple(arguments) != service_inventory_plan_arguments(plan):
        raise ValueError("service inventory plan arguments must be canonical")
    return plan


def sanitize_service_banner(value: bytes | str, max_bytes: int = 4096) -> str:
    """Bound a peer observation using the shared redactor, never interpret it.

    Redact before truncating the rendered text so truncation cannot expose the
    tail of a known secret. Remove terminal escapes and non-printing controls.
    This is a display boundary, not a second credential-redaction policy.
    """
    if not 1 <= max_bytes <= 4096:
        raise ValueError("peer observation byte bound is invalid")
    text = (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))", "", text)
    text = "".join(
        character if character.isprintable() or character == "\n" else " "
        for character in text
    )
    text = redact_sensitive_text(text)
    text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    # A clipped redaction placeholder can look like another secret assignment
    # to the shared sanitizer. Drop only that unfinished trailing token rather
    # than emit a non-idempotent value or introduce another redaction policy.
    while text and redact_sensitive_text(text) != text:
        parts = text.rsplit(None, 1)
        text = parts[0] if len(parts) == 2 else ""
    return text


class ServiceInventoryPeerObservation(_Model):
    text: str = Field(min_length=1, max_length=4096)
    source: Literal["peer_banner"] = "peer_banner"
    confidence: Literal["unverified"] = "unverified"

    @field_validator("text")
    @classmethod
    def _sanitized(cls, value):
        if sanitize_service_banner(value) != value:
            raise ValueError("peer observation must be bounded sanitized text")
        return value


class ServiceInventoryRecord(_Model):
    authority: str = Field(min_length=3, max_length=259)
    probe: Probe
    reachability: Reachability
    probe_status: ProbeStatus
    bytes_sent: int = Field(0, strict=True, ge=0, le=512)
    bytes_received: int = Field(0, strict=True, ge=0, le=4096)
    observation: ServiceInventoryPeerObservation | None = None
    http_status: int | None = Field(None, strict=True, ge=100, le=599)
    truncated: bool = Field(False, strict=True)

    @model_validator(mode="after")
    def _consistent(self):
        if self.probe == "connect":
            if (
                self.probe_status != "not_requested"
                or self.bytes_sent
                or self.bytes_received
                or self.observation
                or self.http_status
                or self.truncated
            ):
                raise ValueError("connect-only records cannot claim probe evidence")
        elif self.reachability != "open":
            if (
                self.probe_status != "untested"
                or self.bytes_sent
                or self.bytes_received
                or self.observation
                or self.http_status
                or self.truncated
            ):
                raise ValueError("non-open endpoints cannot claim protocol evidence")
        elif self.probe_status == "not_requested":
            raise ValueError("selected probes require an explicit outcome")
        if self.observation and (
            not self.bytes_received or self.probe_status in {"empty", "untested"}
        ):
            raise ValueError("peer observations require received probe data")
        if self.probe == "banner" and (self.bytes_sent or self.http_status):
            raise ValueError(
                "passive banner probes cannot transmit or claim HTTP status"
            )
        if (
            self.probe == "banner"
            and self.probe_status == "observed"
            and self.observation is None
        ):
            raise ValueError("observed banner requires its peer observation")
        if self.http_status is not None and (
            self.probe != "http-head"
            or self.probe_status != "observed"
            or not self.bytes_received
        ):
            raise ValueError("HTTP status requires an observed HTTP response")
        if (
            self.probe == "http-head"
            and self.probe_status == "observed"
            and self.http_status is None
        ):
            raise ValueError("observed HTTP response requires a status")
        if self.probe_status == "empty" and self.bytes_received:
            raise ValueError("empty probe cannot have received bytes")
        return self


class ServiceInventoryExecution(_Model):
    schema_version: Literal["service-inventory-execution/v1"] = (
        "service-inventory-execution/v1"
    )
    plan_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    stop_reason: StopReason | None = None
    records: tuple[ServiceInventoryRecord, ...] = Field(
        (), max_length=MAX_NATIVE_ENDPOINTS
    )

    @model_validator(mode="after")
    def _consistent(self):
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("execution finish precedes its start")
        if (self.finished_at is None) != (self.stop_reason is None):
            raise ValueError("finished execution requires its stop reason")
        if len({record.authority for record in self.records}) != len(self.records):
            raise ValueError("duplicate service endpoint evidence")
        return self


def _bounded_json(value: object) -> bytes:
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_SERVICE_INVENTORY_EXECUTION_BYTES:
        raise ValueError("service inventory exceeds serialized output bound")
    return payload


def validate_service_inventory_execution(
    plan: ServiceInventoryPlan, execution: dict | ServiceInventoryExecution
) -> dict:
    """Rebuild exact bounded evidence and reject authority or outcome invention."""
    value = (
        execution.model_dump(mode="json", by_alias=True)
        if isinstance(execution, ServiceInventoryExecution)
        else execution
    )
    _bounded_json(value)
    parsed = ServiceInventoryExecution.model_validate(value)
    if parsed.plan_key != plan.plan_key:
        raise ValueError("service inventory execution differs from its plan")
    endpoints = {endpoint.authority: endpoint for endpoint in plan.endpoints}
    for record in parsed.records:
        endpoint = endpoints.get(record.authority)
        if endpoint is None or endpoint.probe != record.probe:
            raise ValueError("service inventory endpoint differs from admission")
        if record.bytes_received > plan.capture_bytes_per_endpoint or (
            record.observation is not None
            and len(record.observation.text.encode()) > plan.capture_bytes_per_endpoint
        ):
            raise ValueError("service inventory capture exceeds its plan bound")
        if record.probe == "http-head" and record.bytes_sent not in {
            0,
            len(plan.http_head_request(endpoint)),
        }:
            raise ValueError("HTTP bytes differ from the fixed request")
        if (
            record.probe == "http-head"
            and record.probe_status in {"observed", "empty", "malformed"}
            and not record.bytes_sent
        ):
            raise ValueError("HTTP response requires its fixed request")
    if (
        sum(record.reachability != "untested" for record in parsed.records)
        > plan.native_network.limits.max_connections
    ):
        raise ValueError("service inventory exceeds native connection budget")
    if (
        sum(record.bytes_sent + record.bytes_received for record in parsed.records)
        > plan.native_network.limits.max_bytes
    ):
        raise ValueError("service inventory exceeds native byte budget")
    result = parsed.model_dump(mode="json", by_alias=True)
    _bounded_json(result)
    return result


def serialize_service_inventory_execution(
    plan: ServiceInventoryPlan, execution: dict | ServiceInventoryExecution
) -> bytes:
    return _bounded_json(validate_service_inventory_execution(plan, execution))


class ServiceInventoryPinnedIdentity(_Model):
    """Trusted collector input, deliberately absent from execution records."""

    authority: str = Field(min_length=3, max_length=259)
    address: str = Field(max_length=15)
    target_identity: str | None = Field(None, min_length=1, max_length=256)

    @field_validator("address")
    @classmethod
    def _address(cls, value):
        if str(ipaddress.IPv4Address(value)) != value:
            raise ValueError("pinned address must be canonical IPv4")
        return value


def service_inventory_pinned_identities(
    plan: ServiceInventoryPlan, resolution: dict
) -> tuple[ServiceInventoryPinnedIdentity, ...]:
    """Derive pins from existing, independently verified lease evidence.

    The caller owns current access, artifact provenance and evidence-digest
    verification. This pure helper only binds that evidence to the native
    selection and checks that its final counters are usable; it grants no
    authority and cannot authenticate a workload-supplied dictionary.
    """
    from moonmind.security.native_tcp_gateway import validate_dial_outcomes

    if not isinstance(resolution, dict):
        raise ValueError(  # noqa: TRY004 - unusable evidence
            "service inventory requires retained native attestation"
        )
    _bounded_json(resolution)
    native = plan.native_network.model_dump(mode="json", by_alias=True)
    retained_limits = type(plan.native_network.limits).model_validate(
        resolution.get("nativeLimits")
    )
    if (
        resolution.get("nativeSelectionDigest") != "sha256:" + _key(native)
        or retained_limits != plan.native_network.limits
        or type(resolution.get("nativeConnectTimeoutSeconds")) is not int
        or resolution["nativeConnectTimeoutSeconds"]
        != plan.options.connect_timeout_seconds
        or resolution.get("resolution") != "validated_pinned_job_snapshot"
        or resolution.get("coverage") != "complete"
        or resolution.get("limitations") != []
        or resolution.get("denialDiagnostics") != []
        or "evidenceError" in resolution
        or any(
            type(resolution.get(name)) is not int or resolution[name] != 0
            for name in ("deniedConnectionCount", "transportErrorCount")
        )
    ):
        raise ValueError("native attestation is incomplete or differs from selection")
    addresses, identities, counters = (
        resolution.get("resolvedAddresses"),
        resolution.get("targetIdentities"),
        resolution.get("nativeNetwork"),
    )
    hosts = {endpoint.host for endpoint in plan.endpoints}
    private_routes = {
        host: (target.target_network_ref, target.target_container_ref)
        for target in plan.native_network.targets
        if target.target_container_ref
        for host, _ in target.endpoints()
    }
    if (
        not isinstance(addresses, dict)
        or set(addresses) != hosts
        or not isinstance(identities, dict)
        or set(identities) != {route[1] for route in private_routes.values()}
        or any(
            not isinstance(identity, str) or not 1 <= len(identity) <= 256
            for identity in identities.values()
        )
        or not isinstance(counters, dict)
    ):
        raise ValueError("native pinned identities differ from admitted targets")
    for host, values in addresses.items():
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
            raise ValueError("native resolved addresses are unavailable or unbounded")
        if any(
            not isinstance(value, str) or str(ipaddress.IPv4Address(value)) != value
            for value in values
        ) or len(set(values)) != len(values):
            raise ValueError("native resolved addresses must be canonical IPv4")
        try:
            numeric = ipaddress.IPv4Address(host)
        except ValueError:
            numeric = None
        if numeric is not None and tuple(values) != (str(numeric),):
            raise ValueError("native numeric identity differs from selected address")
    expected_endpoints = [
        {
            "authority": endpoint.authority,
            "address": min(addresses[endpoint.host]),
            "port": endpoint.port,
        }
        for endpoint in plan.endpoints
    ]
    retained_endpoints = counters.get("endpoints")
    if (
        not isinstance(retained_endpoints, list)
        or len(retained_endpoints) != len(expected_endpoints)
        or any(
            not isinstance(endpoint, dict)
            or set(endpoint) != {"authority", "address", "port"}
            or not isinstance(endpoint["authority"], str)
            or not isinstance(endpoint["address"], str)
            or type(endpoint["port"]) is not int
            for endpoint in retained_endpoints
        )
        # The gateway orders authority strings; plans order numeric ports.
        # Compare exact rows without discarding duplicates or rewriting evidence.
        or sorted(retained_endpoints, key=lambda endpoint: endpoint["authority"])
        != sorted(expected_endpoints, key=lambda endpoint: endpoint["authority"])
        or type(counters.get("schemaVersion")) is not int
        or counters["schemaVersion"] != 1
        or counters.get("status") not in {"running", "closed"}
        or not isinstance(resolution.get("gatewayRef"), str)
        or not resolution["gatewayRef"]
        or counters.get("leaseId") != resolution["gatewayRef"]
        or not isinstance(counters.get("configDigest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", counters["configDigest"])
        or type(counters.get("deadlineUnix")) not in (int, float)
        or not math.isfinite(counters["deadlineUnix"])
        or counters["deadlineUnix"] != resolution.get("expiresAt")
        or type(counters.get("pid")) is not int
        or counters["pid"] < 1
        or not isinstance(counters.get("processStart"), str)
        or not counters["processStart"].isdigit()
    ):
        raise ValueError(
            "native gateway identity or endpoint evidence differs from selection"
        )
    for name, maximum in (
        ("attempts", plan.native_network.limits.max_connections),
        ("activeConnections", 0),
        ("bytesReserved", plan.native_network.limits.max_bytes),
    ):
        if type(counters.get(name)) is not int or not 0 <= counters[name] <= maximum:
            raise ValueError(
                "native counters are active, unavailable or outside admitted limits"
            )
    for name in ("denials", "transportErrors"):
        counts = counters.get(name)
        if (
            not isinstance(counts, dict)
            or len(counts) > 32
            or any(
                not isinstance(key, str)
                or len(key) > 64
                or type(value) is not int
                or value != 0
                for key, value in counts.items()
            )
        ):
            raise ValueError("native transport has denied or uncertain outcomes")
    validate_dial_outcomes(counters)
    if (
        not isinstance(counters.get("dialOutcomes"), dict)
        or sum(counters["dialOutcomes"].values()) != counters["attempts"]
    ):
        raise ValueError("native dial outcomes are incomplete")
    return tuple(
        ServiceInventoryPinnedIdentity(
            authority=endpoint.authority,
            address=min(addresses[endpoint.host]),
            target_identity=(
                _key(
                    {
                        "networkRef": private_routes[endpoint.host][0],
                        "containerRef": private_routes[endpoint.host][1],
                        "containerIdentity": identities[
                            private_routes[endpoint.host][1]
                        ],
                    }
                )
                if endpoint.host in private_routes
                else None
            ),
        )
        for endpoint in plan.endpoints
    )


def validate_service_inventory_receipt(
    plan: ServiceInventoryPlan,
    execution: dict | ServiceInventoryExecution,
    resolution: dict,
) -> None:
    """Check aggregate plausibility, not nonexistent per-endpoint attestation.

    Unknown client-side I/O can precede any gateway reservation. It is never a
    confirmed dial outcome or comparable absence. A contradiction removes
    usable coverage at the caller; it must not rewrite retained observations.
    """
    service_inventory_pinned_identities(plan, resolution)
    parsed = validate_service_inventory_execution(plan, execution)
    counters = resolution["nativeNetwork"]
    known = Counter(
        record["reachability"]
        for record in parsed["records"]
        if record["reachability"] in {"open", "refused", "unreachable", "timed_out"}
    )
    if (
        any(
            count > counters["dialOutcomes"].get(outcome, 0)
            for outcome, count in known.items()
        )
        or sum(known.values()) > counters["attempts"]
    ):
        raise ValueError("service observations exceed trusted native dial outcomes")
    if (
        sum(
            record["bytesSent"] + record["bytesReceived"]
            for record in parsed["records"]
        )
        > counters["bytesReserved"]
    ):
        raise ValueError("service observations exceed trusted native byte reservations")
    if (
        parsed["stopReason"] == "complete"
        and sum(known.values()) == len(plan.endpoints)
        and sum(known.values()) != counters["attempts"]
    ):
        raise ValueError(
            "completed service inventory differs from native attempt count"
        )


class ServiceInventoryEndpointSummary(_Model):
    authority: str = Field(min_length=3, max_length=259)
    probe: Probe
    reachability: Reachability
    probe_status: ProbeStatus
    observation_key: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    http_status: int | None = Field(None, strict=True, ge=100, le=599)
    truncated: bool = Field(False, strict=True)
    reachability_comparable: bool = Field(False, strict=True)
    absence_comparable: bool = Field(False, strict=True)

    @model_validator(mode="after")
    def _consistent(self):
        if self.reachability_comparable and self.reachability not in {
            "open",
            "refused",
        }:
            raise ValueError(
                "uncertain reachability cannot establish comparable coverage"
            )
        if self.absence_comparable and (
            self.reachability != "refused" or not self.reachability_comparable
        ):
            raise ValueError("comparable absence requires confirmed refusal coverage")
        return self


class ServiceInventorySummary(_Model):
    schema_version: Literal["service-inventory/v1"] = "service-inventory/v1"
    plan_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["complete", "partial", "failed"]
    evidence_ref: str = Field(min_length=1, max_length=1024)
    native_network: NativeNetworkSelection
    service_inventory: ServiceInventoryOptions
    attestation_ref: str | None = Field(None, min_length=1, max_length=1024)
    job_state: JobState | None = None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    stop_reason: StopReason | None
    capture_bytes_per_endpoint: int = Field(strict=True, ge=1, le=4096)
    endpoints: tuple[ServiceInventoryEndpointSummary, ...] = Field(
        min_length=1, max_length=MAX_NATIVE_ENDPOINTS
    )
    comparison_key: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    interpretation: Literal[INTERPRETATION] = INTERPRETATION
    limitations: tuple[
        Literal[
            "execution_unavailable",
            "attestation_unavailable",
            "job_not_succeeded",
            "native_receipt_mismatch",
            "incomplete_pinned_identity",
            "incomplete_reachability",
            "incomplete_probe",
            "truncated_observation",
            "run_incomplete",
        ],
        ...,
    ] = ()

    @model_validator(mode="after")
    def _consistent(self):
        plan = build_service_inventory_plan(self.native_network, self.service_inventory)
        if (
            self.plan_key != plan.plan_key
            or self.capture_bytes_per_endpoint != plan.capture_bytes_per_endpoint
            or [(row.authority, row.probe) for row in self.endpoints]
            != [(endpoint.authority, endpoint.probe) for endpoint in plan.endpoints]
        ):
            raise ValueError(
                "service summary settings or endpoints differ from its canonical plan"
            )
        if self.comparison_key is not None and (
            not self.attestation_ref
            or self.attestation_ref == "unavailable"
            or self.job_state != "succeeded"
            or set(self.limitations)
            & {
                "attestation_unavailable",
                "native_receipt_mismatch",
                "incomplete_pinned_identity",
                "job_not_succeeded",
                "execution_unavailable",
            }
        ):
            raise ValueError(
                "comparable service summary requires retained attestation and successful job"
            )
        if len({endpoint.authority for endpoint in self.endpoints}) != len(
            self.endpoints
        ):
            raise ValueError("duplicate service summary endpoint")
        if self.comparison_key is None and any(
            endpoint.reachability_comparable or endpoint.absence_comparable
            for endpoint in self.endpoints
        ):
            raise ValueError("comparable coverage requires trusted pinned identity")
        if self.status == "complete" and (
            self.limitations
            or self.comparison_key is None
            or self.finished_at is None
            or self.stop_reason != "complete"
            or any(
                endpoint.truncated
                or not endpoint.reachability_comparable
                or (
                    endpoint.reachability == "open"
                    and endpoint.probe != "connect"
                    and endpoint.probe_status not in {"observed", "empty"}
                )
                for endpoint in self.endpoints
            )
        ):
            raise ValueError("complete service summary requires full usable coverage")
        if self.finished_at is not None and (
            self.started_at is None or self.finished_at < self.started_at
        ):
            raise ValueError("summary finish precedes its start")
        return self


def summarize_service_inventory(
    plan: ServiceInventoryPlan,
    execution: dict | ServiceInventoryExecution | None,
    evidence_ref: str,
    *,
    pinned_identities: tuple[ServiceInventoryPinnedIdentity, ...] = (),
    attestation_ref: str | None = None,
    job_state: JobState | None = None,
    native_receipt_mismatch: bool = False,
) -> ServiceInventorySummary:
    """Normalize partial progress without manufacturing comparable absence.

    The caller must get pinned identities from trusted collector attestation,
    never workload-controlled execution. Matching configuration alone does not
    make timeout, denial, unknown I/O or omitted endpoints comparable coverage.
    """
    parsed = (
        ServiceInventoryExecution.model_validate(
            validate_service_inventory_execution(plan, execution)
        )
        if execution
        else None
    )
    endpoints = {endpoint.authority: endpoint for endpoint in plan.endpoints}
    pins = {pin.authority: pin for pin in pinned_identities}
    if len(pins) != len(pinned_identities) or set(pins) - set(endpoints):
        raise ValueError("pinned identity differs from admitted endpoint set")
    private = {
        f"{host}:{port}"
        for target in plan.native_network.targets
        if target.target_container_ref
        for host, port in target.endpoints()
    }
    pinned = set(pins) == set(endpoints) and all(
        pins[authority].target_identity for authority in private
    )
    attested = bool(attestation_ref and attestation_ref != "unavailable")
    usable = bool(
        pinned
        and attested
        and job_state == "succeeded"
        and parsed
        and not native_receipt_mismatch
    )
    comparison_key = (
        _key(
            {
                "planKey": plan.plan_key,
                "pinnedIdentities": [
                    pins[authority].model_dump(mode="json", by_alias=True)
                    for authority in sorted(pins)
                ],
            }
        )
        if usable
        else None
    )
    limitations = []
    if not parsed:
        limitations.append("execution_unavailable")
    if not pinned:
        limitations.append("incomplete_pinned_identity")
    if not attested:
        limitations.append("attestation_unavailable")
    if job_state != "succeeded":
        limitations.append("job_not_succeeded")
    if native_receipt_mismatch:
        limitations.append("native_receipt_mismatch")
    if parsed and (parsed.finished_at is None or parsed.stop_reason != "complete"):
        limitations.append("run_incomplete")
    records = {record.authority: record for record in parsed.records} if parsed else {}
    summaries = []
    for endpoint in plan.endpoints:
        record = records.get(endpoint.authority) or ServiceInventoryRecord(
            authority=endpoint.authority,
            probe=endpoint.probe,
            reachability="untested",
            probe_status="not_requested" if endpoint.probe == "connect" else "untested",
        )
        known = record.reachability in {"open", "refused"}
        if not known:
            limitations.append("incomplete_reachability")
        if (
            record.reachability == "open"
            and record.probe != "connect"
            and record.probe_status not in {"observed", "empty"}
        ):
            limitations.append("incomplete_probe")
        if record.truncated:
            limitations.append("truncated_observation")
        summaries.append(
            ServiceInventoryEndpointSummary(
                authority=record.authority,
                probe=record.probe,
                reachability=record.reachability,
                probe_status=record.probe_status,
                observation_key=(
                    _key(record.observation.model_dump(mode="json"))
                    if record.observation
                    else None
                ),
                http_status=record.http_status,
                truncated=record.truncated,
                reachability_comparable=bool(usable and known),
                absence_comparable=bool(usable and record.reachability == "refused"),
            )
        )
    return ServiceInventorySummary(
        plan_key=plan.plan_key,
        status="failed" if not parsed else "partial" if limitations else "complete",
        evidence_ref=evidence_ref,
        native_network=plan.native_network,
        service_inventory=plan.options,
        attestation_ref=attestation_ref,
        job_state=job_state,
        started_at=parsed.started_at if parsed else None,
        finished_at=parsed.finished_at if parsed else None,
        stop_reason=parsed.stop_reason if parsed else None,
        capture_bytes_per_endpoint=plan.capture_bytes_per_endpoint,
        endpoints=tuple(summaries),
        comparison_key=comparison_key,
        limitations=tuple(sorted(set(limitations))),
    )
