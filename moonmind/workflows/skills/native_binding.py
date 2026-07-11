"""Fail-closed selection of trusted native implementations from resolved evidence."""

from dataclasses import dataclass
from typing import Any, Mapping

PR_RESOLVER_CONTRACT = "pr-resolver-core/v1"
PR_RESOLVER_CORE_VERSION = "1"


@dataclass(frozen=True, slots=True)
class NativeBindingDecision:
    eligible: bool
    reason_code: str


def pr_resolver_native_binding(entry: Any) -> NativeBindingDecision:
    def value(obj: Any, *names: str) -> Any:
        for name in names:
            candidate = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
            if candidate is not None:
                return candidate
        return None

    if str(value(entry, "skill_name", "skillName", "name") or "").lower() != "pr-resolver":
        return NativeBindingDecision(False, "skill_identity_mismatch")
    if not value(entry, "content_digest", "contentDigest") and not value(entry, "content_ref", "contentRef"):
        return NativeBindingDecision(False, "immutable_content_evidence_missing")
    provenance = value(entry, "provenance")
    if provenance is None or not value(provenance, "source_kind", "sourceKind"):
        return NativeBindingDecision(False, "provenance_missing")
    implementation = value(entry, "implementation")
    if implementation is None:
        return NativeBindingDecision(False, "implementation_contract_missing")
    if value(implementation, "contract") != PR_RESOLVER_CONTRACT:
        return NativeBindingDecision(False, "implementation_contract_incompatible")
    if str(value(implementation, "core_version", "coreVersion") or "") != PR_RESOLVER_CORE_VERSION:
        return NativeBindingDecision(False, "resolver_core_incompatible")
    hosts = value(implementation, "supported_hosts", "supportedHosts") or ()
    if "temporal" not in hosts or value(implementation, "native_host_allowed", "nativeHostAllowed") is not True:
        return NativeBindingDecision(False, "native_host_not_permitted")
    return NativeBindingDecision(True, "trusted_native_binding")
