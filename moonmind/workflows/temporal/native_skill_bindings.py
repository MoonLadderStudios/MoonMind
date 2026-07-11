"""Trusted bindings between resolved portable Skills and native workflow hosts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pr_resolver_core import IMPLEMENTATION_CONTRACT, RESOLVER_CORE_VERSION


@dataclass(frozen=True, slots=True)
class NativeSkillBindingDecision:
    eligible: bool
    host: str
    reason_code: str
    identity: dict[str, Any]


def _field(value: Any, *names: str) -> Any:
    for name in names:
        candidate = (
            value.get(name)
            if isinstance(value, Mapping)
            else getattr(value, name, None)
        )
        if candidate is not None:
            return candidate
    return None


def _implementation_payload(entry: Any) -> Mapping[str, Any]:
    implementation = _field(entry, "implementation")
    if isinstance(implementation, Mapping):
        return implementation
    if implementation is not None and hasattr(implementation, "model_dump"):
        return implementation.model_dump(by_alias=True, mode="json")
    return {}


def evaluate_pr_resolver_native_binding(entry: Any) -> NativeSkillBindingDecision:
    """Replay-only evaluator for histories that predate skill-owned execution."""

    name = str(_field(entry, "skill_name", "skillName", "name") or "").strip()
    provenance = _field(entry, "provenance")
    source_value = _field(provenance, "source_kind", "sourceKind")
    source_kind = str(getattr(source_value, "value", source_value) or "").strip()
    implementation = _implementation_payload(entry)
    contract = str(implementation.get("contract") or "").strip()
    supported_hosts = {
        str(host).strip().lower()
        for host in (
            implementation.get("supportedHosts")
            or implementation.get("supported_hosts")
            or []
        )
    }
    eligible_flag = bool(
        implementation.get("nativeHostEligible")
        if "nativeHostEligible" in implementation
        else implementation.get("native_host_eligible")
    )
    policy = str(
        implementation.get("nativeHostPolicy")
        or implementation.get("native_host_policy")
        or ""
    ).strip()
    content_digest = str(_field(entry, "content_digest", "contentDigest") or "").strip()
    content_ref = str(_field(entry, "content_ref", "contentRef") or "").strip()
    identity = {
        "skillName": name,
        "implementationContract": contract,
        "resolverCoreVersion": RESOLVER_CORE_VERSION,
        "contentDigest": content_digest or None,
        "contentRef": content_ref or None,
        "sourceKind": source_kind or None,
        "nativeHostPolicy": policy or None,
    }
    checks = (
        (name == "pr-resolver", "skill_name_mismatch"),
        (contract == IMPLEMENTATION_CONTRACT, "implementation_contract_mismatch"),
        ("temporal" in supported_hosts, "temporal_host_not_supported"),
        (eligible_flag, "native_host_not_eligible"),
        (policy == "moonmind_trusted", "native_host_policy_denied"),
        (source_kind == "built_in", "untrusted_skill_source"),
        (bool(content_digest and content_ref), "immutable_content_evidence_missing"),
    )
    for allowed, reason in checks:
        if not allowed:
            return NativeSkillBindingDecision(False, "cli", reason, identity)
    return NativeSkillBindingDecision(
        True, "temporal", "native_binding_accepted", identity
    )


def require_skill_owned_pr_resolver_execution(
    entry: Any,
) -> NativeSkillBindingDecision:
    """Route every new resolver run through its exact resolved Skill bundle."""

    legacy = evaluate_pr_resolver_native_binding(entry)
    return NativeSkillBindingDecision(
        eligible=False,
        host="cli",
        reason_code="skill_owned_execution_required",
        identity=legacy.identity,
    )
