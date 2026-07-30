"""Trusted-backend egress enforcement and attestation (MoonMind#3516).

This module realizes the *semantic* half of restricted egress that must be
identical in every host: it compiles an immutable :class:`EgressProfile` into a
deterministic, content-addressed default-deny rule set, evaluates a connection
attempt against that rule set (the model a real firewall/gateway enforces at the
network layer), guards the launch spec against bypass authority, and produces a
bounded :class:`EgressAttestation` evidence record.

A declared or reflected Docker network is **not** enforcement.  A network ref is
attestable only when:

1. the selected profile is immutable and ``validated``;
2. its compiled rule set passes the negative conformance probe (allowed
   destinations pass; representative bypass/forbidden destinations fail); and
3. the trusted backend confirms the enforcing network/gateway state exists.

The concrete host-level realization (egress proxy + firewall chains that force
container traffic through it, IPv6 disablement, metadata blackholing) is
documented in ``docs/Omnigent/RestrictedEgressEnforcement.md``.  Backends that
cannot enforce at the network layer must declare ``enforcing=False`` and will
never yield an attested ref, so the system fails closed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from moonmind.omnigent.egress_profiles import (
    EgressProfile,
    is_implicitly_denied,
    get_egress_profile,
)

ENFORCER_VERSION = "egress-enforcer/1"

# Capabilities / flags that would let a workload rewrite routing or firewall
# state, or otherwise bypass the gateway, and must never be granted.
FORBIDDEN_CAPABILITIES = frozenset(
    {"NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_RAWIO", "BPF"}
)


# ---------------------------------------------------------------------------
# Compiled rule set
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EgressRule:
    action: Literal["allow", "deny"]
    kind: Literal["range", "cidr", "dns"]
    value: str
    ports: tuple[int, ...] = ()
    protocols: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "kind": self.kind,
            "value": self.value,
            "ports": list(self.ports),
            "protocols": list(self.protocols),
            "note": self.note,
        }


@dataclass(frozen=True)
class EgressRuleset:
    profile_ref: str
    profile_digest: str
    rules: tuple[EgressRule, ...]
    default_action: Literal["deny"] = "deny"
    ipv6_policy: Literal["deny", "allow_listed"] = "deny"

    @property
    def applied_rule_digest(self) -> str:
        canonical = json.dumps(
            {
                "profileRef": self.profile_ref,
                "profileDigest": self.profile_digest,
                "defaultAction": self.default_action,
                "ipv6Policy": self.ipv6_policy,
                "rules": [rule.as_dict() for rule in self.rules],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def compile_ruleset(profile: EgressProfile) -> EgressRuleset:
    """Compile a validated profile into an ordered default-deny rule set.

    Ordering is significant: implicit internal-range denials are emitted first
    so no allow-list entry can ever open loopback/metadata/control-plane space,
    followed by explicit allow rules, terminating in an implicit default deny.
    """

    rules: list[EgressRule] = []
    # 1. Non-overridable denials evaluated before any allow.
    for cidr in _implicit_deny_cidrs(profile):
        rules.append(
            EgressRule(action="deny", kind="range", value=cidr, note="implicit-internal-range")
        )
    ports = tuple(sorted(set(profile.allowed_ports)))
    protocols = tuple(sorted(set(profile.allowed_protocols)))
    # 2. Explicit allows.
    for cidr in profile.allowed_cidrs:
        rules.append(
            EgressRule(
                action="allow",
                kind="cidr",
                value=cidr,
                ports=ports,
                protocols=protocols,
                note="allowed-cidr",
            )
        )
    for name in profile.allowed_dns:
        rules.append(
            EgressRule(
                action="allow",
                kind="dns",
                value=name,
                ports=ports,
                protocols=protocols,
                note=f"allowed-dns:{profile.resolution}",
            )
        )
    return EgressRuleset(
        profile_ref=profile.ref,
        profile_digest=profile.digest,
        rules=tuple(rules),
        default_action="deny",
        ipv6_policy=profile.ipv6_policy,
    )


def _implicit_deny_cidrs(profile: EgressProfile) -> tuple[str, ...]:
    from moonmind.omnigent.egress_profiles import IMPLICIT_DENY_CIDRS

    if profile.allow_internal_ranges:
        # Even with a narrow opt-in, keep loopback and metadata blackholed.
        return ("127.0.0.0/8", "::1/128", "169.254.169.254/32")
    return IMPLICIT_DENY_CIDRS


# ---------------------------------------------------------------------------
# Connection evaluation (network-layer model)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConnectionAttempt:
    dest_ip: str
    port: int
    host: str | None = None
    protocol: str = "tcp"
    proxy_endpoint: str | None = None
    redirect_from: str | None = None


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    reason: str
    matched_rule: EgressRule | None = None


def _match_dns(host: str | None, pattern: str) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) and host != pattern[2:]
    return host == pattern


def _normalize_ip(dest_ip: str) -> ipaddress._BaseAddress | None:
    try:
        parsed = ipaddress.ip_address(dest_ip)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


Resolver = Callable[[str], Iterable[str]]


def evaluate(
    ruleset: EgressRuleset,
    profile: EgressProfile,
    attempt: ConnectionAttempt,
    *,
    resolver: Resolver | None = None,
) -> EgressDecision:
    """Return the allow/deny decision the enforced network layer must produce."""

    parsed = _normalize_ip(attempt.dest_ip)
    if parsed is None:
        return EgressDecision(False, "unparseable-destination")

    # Redirects and proxying never bypass the check: the *effective* destination
    # is always re-evaluated against the allow-list.
    if attempt.proxy_endpoint is not None:
        if attempt.proxy_endpoint not in profile.proxy_endpoints:
            return EgressDecision(False, "proxy-not-approved")

    # IPv6 policy: deny non-mapped IPv6 unless explicitly allow-listed.
    original = ipaddress.ip_address(attempt.dest_ip) if _safe(attempt.dest_ip) else None
    if (
        isinstance(original, ipaddress.IPv6Address)
        and original.ipv4_mapped is None
        and profile.ipv6_policy == "deny"
    ):
        return EgressDecision(False, "ipv6-denied")

    # Non-overridable internal ranges (loopback, metadata, docker-gw, ...).
    if is_implicitly_denied(str(parsed)) and not _explicitly_allowed_internal(
        profile, parsed
    ):
        reason = "metadata-endpoint" if str(parsed) in _metadata_ips() else "forbidden-internal-range"
        return EgressDecision(False, reason)

    if attempt.port not in profile.allowed_ports:
        return EgressDecision(False, "port-not-allowed")
    if attempt.protocol not in profile.allowed_protocols:
        return EgressDecision(False, "protocol-not-allowed")

    # Host-name allow-list, with DNS-rebinding / TOCTOU defence.
    if attempt.host is not None:
        for rule in ruleset.rules:
            if rule.action == "allow" and rule.kind == "dns" and _match_dns(
                attempt.host, rule.value
            ):
                if profile.resolution in {"revalidate", "pin"} and resolver is not None:
                    resolved = {str(_normalize_ip(ip) or ip) for ip in resolver(attempt.host)}
                    if str(parsed) not in resolved:
                        return EgressDecision(False, "dns-rebinding", rule)
                return EgressDecision(True, "allowed-dns", rule)

    # CIDR allow-list (covers pinned direct-IP destinations).
    for rule in ruleset.rules:
        if rule.action == "allow" and rule.kind == "cidr":
            try:
                if parsed in ipaddress.ip_network(rule.value, strict=False):
                    return EgressDecision(True, "allowed-cidr", rule)
            except ValueError:
                continue

    # A direct IP connection when only DNS names are allowed is a bypass.
    if attempt.host is None and profile.allowed_dns and not profile.allowed_cidrs:
        return EgressDecision(False, "direct-ip-not-allowed")

    return EgressDecision(False, "not-in-allowlist")


def _safe(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _metadata_ips() -> frozenset[str]:
    from moonmind.omnigent.egress_profiles import METADATA_ENDPOINTS

    return frozenset(METADATA_ENDPOINTS)


def _explicitly_allowed_internal(
    profile: EgressProfile, parsed: ipaddress._BaseAddress
) -> bool:
    if not profile.allow_internal_ranges:
        return False
    # Loopback and metadata are never allow-listable.
    if parsed.is_loopback or str(parsed) in _metadata_ips():
        return False
    for cidr in profile.allowed_cidrs:
        try:
            if parsed in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Launch-constraint guard (AC4)
# ---------------------------------------------------------------------------
class EgressLaunchConstraintError(ValueError):
    """Raised when a launch spec asks for authority that could bypass egress."""


def validate_launch_constraints(spec: Mapping[str, Any], *, approved_network: str) -> None:
    """Reject launch specs that could bypass the enforcing network namespace.

    The browser, workflow payload and untrusted host process must never receive
    authority to select raw Docker networks, firewall commands, host routes or
    bypass flags.
    """

    network_mode = str(spec.get("network_mode") or spec.get("networkMode") or "").lower()
    if network_mode in {"host", "none", "container", "bridge"}:
        raise EgressLaunchConstraintError(
            f"network_mode={network_mode!r} bypasses the enforcing network"
        )
    if spec.get("privileged"):
        raise EgressLaunchConstraintError("privileged containers bypass egress enforcement")

    cap_add = {str(cap).upper() for cap in spec.get("cap_add", spec.get("capAdd", []) or [])}
    illegal = cap_add & FORBIDDEN_CAPABILITIES
    if illegal:
        raise EgressLaunchConstraintError(
            f"forbidden capabilities requested: {sorted(illegal)}"
        )

    networks = spec.get("networks") or spec.get("network_refs") or []
    if isinstance(networks, (str, bytes)):
        networks = [networks]
    extra = [str(n) for n in networks if str(n) != approved_network]
    if extra:
        raise EgressLaunchConstraintError(
            f"unapproved secondary network attachment: {extra}"
        )

    if spec.get("extra_hosts") or spec.get("extraHosts"):
        raise EgressLaunchConstraintError("extra_hosts can redirect around the gateway")
    if spec.get("routes") or spec.get("sysctls"):
        raise EgressLaunchConstraintError("host routes / sysctls can bypass egress rules")
    devices = spec.get("devices") or []
    if devices:
        raise EgressLaunchConstraintError("device passthrough is not permitted")


# ---------------------------------------------------------------------------
# Negative conformance probe (AC12)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConformanceCase:
    name: str
    attempt: ConnectionAttempt
    expect_allowed: bool


@dataclass(frozen=True)
class ConformanceReport:
    passed: bool
    total: int
    failures: tuple[str, ...]
    denied_counters: dict[str, int]


def _static_resolver(profile: EgressProfile) -> Resolver:
    # A deterministic resolver for conformance: allowed hosts resolve to a stable
    # public test IP; everything else resolves to an attacker-controlled address.
    allowed = {name: f"203.0.113.{10 + i}" for i, name in enumerate(profile.allowed_dns)}

    def resolve(host: str) -> list[str]:
        return [allowed.get(host.lower().rstrip("."), "203.0.113.250")]

    return resolve


def bypass_conformance_cases(profile: EgressProfile) -> tuple[ConformanceCase, ...]:
    """Representative allowed + bypass/forbidden destinations for a profile."""

    resolver = _static_resolver(profile)
    cases: list[ConformanceCase] = []
    port = next(iter(sorted(profile.allowed_ports)))
    # Allowed positive cases.
    for name in profile.allowed_dns:
        ip = resolver(name)[0]
        cases.append(
            ConformanceCase(
                f"allow-{name}",
                ConnectionAttempt(dest_ip=ip, port=port, host=name),
                expect_allowed=True,
            )
        )
    # Bypass / forbidden negative cases.
    forbidden = {
        "metadata": "169.254.169.254",
        "loopback": "127.0.0.1",
        "docker-gw": "172.17.0.1",
        "private": "10.0.0.5",
        "link-local": "169.254.10.10",
        "ipv4-mapped-metadata": "::ffff:169.254.169.254",
    }
    for label, ip in forbidden.items():
        cases.append(
            ConformanceCase(
                f"deny-{label}",
                ConnectionAttempt(dest_ip=ip, port=port, host=None),
                expect_allowed=False,
            )
        )
    if profile.allowed_dns:
        allowed_name = profile.allowed_dns[0]
        # Direct IP to a public address with no hostname is denied when only DNS
        # names are allowed.
        cases.append(
            ConformanceCase(
                "deny-direct-ip",
                ConnectionAttempt(dest_ip="198.51.100.7", port=port, host=None),
                expect_allowed=False,
            )
        )
        # DNS rebinding: allowed host name but the resolved answer is attacker IP.
        cases.append(
            ConformanceCase(
                "deny-dns-rebinding",
                ConnectionAttempt(dest_ip="198.51.100.9", port=port, host=allowed_name),
                expect_allowed=False,
            )
        )
        # Redirect from an allowed host to an unapproved host is denied because
        # the effective destination is re-evaluated.
        cases.append(
            ConformanceCase(
                "deny-redirect",
                ConnectionAttempt(
                    dest_ip="198.51.100.11",
                    port=port,
                    host="evil.example.com",
                    redirect_from=allowed_name,
                ),
                expect_allowed=False,
            )
        )
        # Disallowed port to an otherwise-allowed host.
        cases.append(
            ConformanceCase(
                "deny-bad-port",
                ConnectionAttempt(dest_ip=resolver(allowed_name)[0], port=9999, host=allowed_name),
                expect_allowed=False,
            )
        )
    return tuple(cases)


def run_conformance(
    profile: EgressProfile,
    ruleset: EgressRuleset | None = None,
    *,
    cases: Sequence[ConformanceCase] | None = None,
) -> ConformanceReport:
    ruleset = ruleset or compile_ruleset(profile)
    cases = tuple(cases) if cases is not None else bypass_conformance_cases(profile)
    resolver = _static_resolver(profile)
    failures: list[str] = []
    denied: dict[str, int] = {}
    for case in cases:
        decision = evaluate(ruleset, profile, case.attempt, resolver=resolver)
        if decision.allowed != case.expect_allowed:
            failures.append(case.name)
        if not decision.allowed:
            denied[decision.reason] = denied.get(decision.reason, 0) + 1
    return ConformanceReport(
        passed=not failures,
        total=len(cases),
        failures=tuple(failures),
        denied_counters=denied,
    )


# ---------------------------------------------------------------------------
# Attestation / evidence (AC5, AC6, AC7, AC10)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EgressAttestation:
    profile_id: str
    profile_version: int
    profile_digest: str
    profile_ref: str
    backend_ref: str
    backend_version: str
    enforcer_version: str
    network_ref: str
    applied_rule_digest: str
    validation_result: Literal["attested", "failed"]
    validated_at: str
    attachment_ref: str | None
    denied_counters: dict[str, int]
    cleanup_state: str
    security_review_ref: str | None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def attested(self) -> bool:
        return self.validation_result == "attested"

    def to_evidence(self) -> dict[str, Any]:
        """Bounded, redacted evidence record — no payloads, credentials or logs."""

        return {
            "profileRef": self.profile_ref,
            "profileDigest": self.profile_digest,
            "backendRef": self.backend_ref,
            "backendVersion": self.backend_version,
            "enforcerVersion": self.enforcer_version,
            "networkRef": self.network_ref,
            "appliedRuleDigest": self.applied_rule_digest,
            "validationResult": self.validation_result,
            "validatedAt": self.validated_at,
            "attachmentRef": self.attachment_ref,
            "deniedCounters": dict(self.denied_counters),
            "cleanupState": self.cleanup_state,
            "securityReviewRef": self.security_review_ref,
            "reasons": list(self.reasons),
        }


def attest_profile(
    profile: EgressProfile | None,
    *,
    network_ref: str,
    backend_ref: str,
    now: str,
    backend_enforcing: bool = True,
    attachment_ref: str | None = None,
    cleanup_state: str = "owned",
) -> EgressAttestation:
    """Produce an evidence-backed attestation, failing closed on any gap."""

    reasons: list[str] = []
    if profile is None:
        return _failed_attestation(
            network_ref, backend_ref, now, attachment_ref, ("profile-missing",)
        )

    ruleset = compile_ruleset(profile)
    if profile.validation_state != "validated":
        reasons.append(f"profile-not-validated:{profile.validation_state}")
    if not backend_enforcing:
        reasons.append("backend-non-enforcing")
    report = run_conformance(profile, ruleset)
    if not report.passed:
        reasons.append(f"conformance-failed:{','.join(report.failures)}")

    result: Literal["attested", "failed"] = "failed" if reasons else "attested"
    return EgressAttestation(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        profile_digest=profile.digest,
        profile_ref=profile.ref,
        backend_ref=backend_ref,
        backend_version=backend_ref,
        enforcer_version=ENFORCER_VERSION,
        network_ref=network_ref,
        applied_rule_digest=ruleset.applied_rule_digest,
        validation_result=result,
        validated_at=now,
        attachment_ref=attachment_ref,
        denied_counters=report.denied_counters,
        cleanup_state=cleanup_state,
        security_review_ref=profile.security_review_ref,
        reasons=tuple(reasons),
    )


def _failed_attestation(
    network_ref: str,
    backend_ref: str,
    now: str,
    attachment_ref: str | None,
    reasons: tuple[str, ...],
) -> EgressAttestation:
    return EgressAttestation(
        profile_id="",
        profile_version=0,
        profile_digest="",
        profile_ref="",
        backend_ref=backend_ref,
        backend_version=backend_ref,
        enforcer_version=ENFORCER_VERSION,
        network_ref=network_ref,
        applied_rule_digest="",
        validation_result="failed",
        validated_at=now,
        attachment_ref=attachment_ref,
        denied_counters={},
        cleanup_state="none",
        security_review_ref=None,
        reasons=reasons,
    )


async def attest_enforced_networks(
    policies: Iterable[Any],
    *,
    network_ready: Callable[[str], Any],
    backend_ref: str,
    now: str,
    backend_enforcing: bool = True,
) -> list[EgressAttestation]:
    """Attest each policy's egress profile against live backend network state.

    Only policies whose profile is validated, whose compiled rule set passes
    conformance, and whose enforcing network is live yield an ``attested``
    result.  Everything else fails closed.
    """

    attestations: list[EgressAttestation] = []
    for policy in policies:
        if not getattr(policy, "enabled", False) or not getattr(
            policy, "enforced_egress", False
        ):
            continue
        network_ref = policy.network_ref
        profile = get_egress_profile(getattr(policy, "egress_profile_ref", ""))
        ready = await network_ready(network_ref)
        if not ready:
            attestations.append(
                _failed_attestation(
                    network_ref, backend_ref, now, None, ("network-not-ready",)
                )
            )
            continue
        attestations.append(
            attest_profile(
                profile,
                network_ref=network_ref,
                backend_ref=backend_ref,
                now=now,
                backend_enforcing=backend_enforcing,
            )
        )
    return attestations
