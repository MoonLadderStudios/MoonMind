"""Shared security contracts, imported lazily to keep settings initialization safe."""

from importlib import import_module

_EGRESS_EXPORTS = {
    "EGRESS_EVIDENCE_DIGEST_KEY",
    "EgressEvidenceDigestError",
    "EgressEvidenceSecretError",
    "attach_evidence_digest",
    "evidence_content_digest",
    "parse_and_verify_conformance_evidence",
    "publish_conformance_evidence",
    "secret_scan_evidence",
    "serialize_conformance_evidence",
    "verify_evidence_digest",
}


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    module = (
        "egress_conformance_evidence" if name in _EGRESS_EXPORTS else "outbound_scan"
    )
    value = getattr(import_module(f"moonmind.security.{module}"), name)
    globals()[name] = value
    return value


__all__ = [
    "EGRESS_EVIDENCE_DIGEST_KEY",
    "OUTBOUND_SCAN_POLICY_REF",
    "EgressEvidenceDigestError",
    "EgressEvidenceSecretError",
    "OutboundBundleItem",
    "OutboundFinding",
    "OutboundScanDecision",
    "OutboundScanResult",
    "attach_evidence_digest",
    "canonical_outbound_digest",
    "evidence_content_digest",
    "parse_and_verify_conformance_evidence",
    "publish_conformance_evidence",
    "resolve_high_security_mode",
    "scan_outbound_bundle",
    "scan_outbound_text",
    "secret_scan_evidence",
    "serialize_conformance_evidence",
    "verify_evidence_digest",
]
