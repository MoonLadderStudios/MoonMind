"""Versioned outbound-scan contract for native Omnigent request surfaces.

MoonLadderStudios/MoonMind#3637: the native Omnigent composer reaches the
provider through the binding-scoped facade
(``docs/Omnigent/OmnigentBridge.md`` §4.2). Without a scanner at that boundary
the composer could bypass the ``MOONMIND_HIGH_SECURITY_MODE`` outbound-scan
guarantee applied to every other MoonMind-owned send path
(``docs/Security/SecretsSystem.md`` §1.1).

This module is the runtime-neutral, versioned extractor/normalizer the facade
router composes. It:

* enumerates the supported text-bearing native request shapes (ordinary and
  queued/steered messages, supported slash-command text/args, reply/quote text,
  workspace-mention and file-caption text, and elicitation/approval responses);
* extracts the *exact* outbound text with stable, caller-provided locations,
  without logging or persisting the raw value;
* runs the canonical :func:`moonmind.security.scan_outbound_bundle` scanner under
  effective high-security precedence;
* binds the decision to a canonical payload digest so an allow result cannot be
  reused after the content changes;
* fails closed — distinct from a content block — when enforcement cannot be
  performed (unknown/uninspectable shape, a required text field that cannot be
  decoded, or a binary/opaque attachment part that is outside the text-scan
  contract).

Keeping these decisions here (rather than inline in the router) lets them be
unit-tested in isolation and keeps one canonical native-scan contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from moonmind.security import (
    OUTBOUND_SCAN_POLICY_REF,
    OutboundBundleItem,
    resolve_high_security_mode,
    scan_outbound_bundle,
)
from moonmind.utils.logging import redact_sensitive_text

# One canonical version for the supported native request shapes and the
# extraction/normalization rules below. A payload whose shape this version does
# not recognize fails closed in high-security mode rather than forwarding
# unscanned text.
NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION = "moonmind.omnigent.native_outbound_scan.v1"


class NativeScanSurface(StrEnum):
    """Operation surface a native request drives at the facade boundary."""

    MESSAGE = "message"
    ELICITATION_RESPONSE = "elicitation_response"


class NativeScanOutcome(StrEnum):
    """Bounded outcome recorded as scan evidence."""

    ALLOW = "allow"
    BLOCK = "block"
    ENFORCEMENT_UNAVAILABLE = "enforcement_unavailable"


# Content-part ``type`` values that name a binary/opaque payload which cannot be
# inspected as ordinary outbound text. They are outside the text-scan contract:
# in high-security mode the facade fails closed rather than forwarding them with
# a false "scanned" claim (brief §4).
_BINARY_PART_TYPES: frozenset[str] = frozenset(
    {
        "input_file",
        "input_image",
        "input_audio",
        "image",
        "image_url",
        "audio",
        "file",
        "binary",
        "attachment",
        "blob",
    }
)

# Content-part ``type`` values whose ``text`` payload MUST decode to a string.
_TEXT_PART_TYPES: frozenset[str] = frozenset(
    {"text", "input_text", "output_text"}
)

# Characters permitted verbatim in an exposed finding-location path segment. A
# caller controls object keys, so a raw key can otherwise carry a secret-like
# value or newline/control characters into ``detail.findings`` and the durable
# audit; anything outside this safe set is collapsed to ``_`` and the whole
# segment is redacted + length-bounded so a location can never disclose or
# inject content.
_UNSAFE_LOCATION_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")
_LOCATION_SEGMENT_MAX = 64


def _safe_location_key(key: Any) -> str:
    """Return a bounded, non-disclosing path segment for a caller-owned key.

    Object keys are attacker-controlled, so the exposed finding location must
    never copy a raw key: a key could carry credential text or newline/control
    characters that would leak into ``detail.findings`` or permit log injection
    when audited. The key is first passed through the canonical secret redactor,
    then any character outside a conservative safe set is collapsed to ``_`` and
    the result is length-bounded.
    """

    redacted = redact_sensitive_text(str(key))
    safe = _UNSAFE_LOCATION_CHARS.sub("_", redacted)
    if len(safe) > _LOCATION_SEGMENT_MAX:
        safe = safe[:_LOCATION_SEGMENT_MAX]
    return safe or "_"


def _composable_text_fragment(value: Any) -> str | None:
    """Return the outbound text a content-array item contributes, or ``None``.

    Used to reconstruct the semantically composed message so a credential split
    across adjacent text parts (rich-composer segmentation around formatting or
    mentions) cannot bypass the scanner by never appearing whole in one leaf. A
    plain string item contributes itself; a declared text part contributes its
    ``text`` value; every other shape (binary/opaque part, nested object)
    contributes nothing to the composed text.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        part_type = value.get("type")
        if isinstance(part_type, str) and part_type.strip().lower() in _TEXT_PART_TYPES:
            text_value = value.get("text")
            if isinstance(text_value, str):
                return text_value
    return None


class _UndecodableFieldError(Exception):
    """A required text field was present but not a decodable string."""


class _UninspectablePartError(Exception):
    """A binary/opaque content part cannot be scanned as outbound text."""


@dataclass(frozen=True, slots=True)
class NativeScanFinding:
    """Redacted finding metadata: category + safe location only, never a value."""

    category: str
    location: str


@dataclass(frozen=True, slots=True)
class NativeScanEvidence:
    """Bounded, non-disclosing evidence for one native outbound-scan decision."""

    contract_version: str
    surface: str
    high_security_mode: bool
    scanner_policy_ref: str
    payload_digest: str
    outcome: str
    findings: tuple[NativeScanFinding, ...] = ()
    idempotency_key: str | None = None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == NativeScanOutcome.ALLOW.value

    def audit_metadata(self) -> dict[str, Any]:
        """Return bounded audit metadata with no detected value or body."""

        metadata: dict[str, Any] = {
            "scanContractVersion": self.contract_version,
            "scanSurface": self.surface,
            "highSecurityMode": self.high_security_mode,
            "scannerPolicyRef": self.scanner_policy_ref,
            "payloadDigest": self.payload_digest,
            "scanOutcome": self.outcome,
        }
        if self.idempotency_key:
            metadata["idempotencyKey"] = self.idempotency_key
        if self.reason:
            metadata["scanUnavailableReason"] = self.reason
        if self.findings:
            metadata["findingCategories"] = sorted(
                {finding.category for finding in self.findings}
            )
            metadata["findingLocations"] = [
                finding.location for finding in self.findings
            ]
        return metadata


class NativeScanBlockedError(Exception):
    """Secret-like content found; carries redacted category/location evidence."""

    def __init__(self, evidence: NativeScanEvidence) -> None:
        super().__init__("native outbound content blocked by security policy")
        self.evidence = evidence


class NativeScanEnforcementError(Exception):
    """Enforcement could not be performed; fail closed distinct from a block."""

    def __init__(self, evidence: NativeScanEvidence) -> None:
        super().__init__("native outbound scan enforcement is unavailable")
        self.evidence = evidence


def canonical_payload_digest(body: Any) -> str:
    """Return a stable sha256 digest of the exact outbound payload.

    The digest binds a scan decision to one payload so a later retry, queued
    flush, steer, reconnect replay, or delivery reconciliation cannot reuse an
    allow result for changed content. It is computed over a canonical JSON
    serialization (sorted keys, no incidental whitespace); a value that cannot be
    serialized deterministically yields a stable ``unserializable`` sentinel so
    the caller still fails closed rather than silently forwarding.
    """

    try:
        serialized = json.dumps(
            body,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = "\x00unserializable"
    return hashlib.sha256(serialized.encode("utf-8", "surrogatepass")).hexdigest()


def _collect_scannable(
    value: Any, *, prefix: str
) -> list[OutboundBundleItem]:
    """Recursively extract ``(location, text)`` pairs, failing closed on binary.

    Every string leaf is scanned (a safe superset that never misses operator
    text), while a content part declaring a binary/opaque ``type`` raises so the
    caller fails closed instead of forwarding an uninspectable part with a false
    scanned claim. A content part declaring a text ``type`` must carry a string
    ``text`` value; a missing or non-string ``text`` raises the undecodable-field
    error so a malformed ``{"type": "text"}`` part cannot be forwarded with only
    its literal type string scanned. Object keys are attacker-controlled and are
    sanitized before they enter an exposed finding location. For content arrays,
    an extra composed item concatenates the adjacent text fragments so a
    credential split across part boundaries is scanned as the provider receives
    it, in addition to the per-part locations.
    """

    items: list[OutboundBundleItem] = []
    if isinstance(value, str):
        items.append(OutboundBundleItem(location=prefix, content=value))
        return items
    if isinstance(value, Mapping):
        part_type = value.get("type")
        if isinstance(part_type, str):
            normalized = part_type.strip().lower()
            if normalized in _BINARY_PART_TYPES:
                raise _UninspectablePartError(prefix)
            if normalized in _TEXT_PART_TYPES and not isinstance(
                value.get("text"), str
            ):
                raise _UndecodableFieldError(f"{prefix}.text")
        for key, item in value.items():
            items.extend(
                _collect_scannable(item, prefix=f"{prefix}.{_safe_location_key(key)}")
            )
        return items
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        composed_fragments: list[str] = []
        for index, item in enumerate(value):
            items.extend(_collect_scannable(item, prefix=f"{prefix}[{index}]"))
            fragment = _composable_text_fragment(item)
            if fragment:
                composed_fragments.append(fragment)
        # Scan the semantically composed text when two or more adjacent parts
        # contribute text, so a secret spanning part boundaries is caught even
        # though no single leaf contains it whole.
        if len(composed_fragments) > 1:
            items.append(
                OutboundBundleItem(
                    location=f"{prefix}[composed]",
                    content="".join(composed_fragments),
                )
            )
        return items
    if isinstance(value, (bytes, bytearray)):
        # A parsed JSON body never yields bytes; if a caller passes raw bytes the
        # content is not safely decodable as outbound text.
        raise _UndecodableFieldError(prefix)
    return items


def scan_native_outbound(
    *,
    surface: NativeScanSurface | str,
    body: Any,
    idempotency_key: str | None = None,
    high_security_mode: bool | None = None,
    settings: object | None = None,
) -> NativeScanEvidence:
    """Scan a native outbound request, returning bounded, digest-bound evidence.

    Raises :class:`NativeScanBlockedError` on a secret-like finding and
    :class:`NativeScanEnforcementError` when required enforcement cannot be
    performed (unknown/uninspectable shape, undecodable field, binary part, or a
    scanner that errors). When high-security mode is disabled it returns an allow
    result and never mutates the caller's payload.
    """

    surface_value = str(getattr(surface, "value", surface))
    effective_mode = resolve_high_security_mode(high_security_mode, settings=settings)
    digest = canonical_payload_digest(body)

    def _evidence(
        outcome: NativeScanOutcome,
        *,
        findings: tuple[NativeScanFinding, ...] = (),
        reason: str | None = None,
    ) -> NativeScanEvidence:
        return NativeScanEvidence(
            contract_version=NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION,
            surface=surface_value,
            high_security_mode=effective_mode,
            scanner_policy_ref=OUTBOUND_SCAN_POLICY_REF,
            payload_digest=digest,
            outcome=outcome.value,
            findings=findings,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    if not effective_mode:
        return _evidence(NativeScanOutcome.ALLOW)

    # Fail closed on an unknown top-level shape: the supported native surfaces are
    # JSON objects, so a non-mapping body cannot be enumerated for text-bearing
    # parts and must not be forwarded unscanned.
    if not isinstance(body, Mapping):
        raise NativeScanEnforcementError(
            _evidence(
                NativeScanOutcome.ENFORCEMENT_UNAVAILABLE, reason="unknown_schema"
            )
        )

    try:
        items = _collect_scannable(body, prefix="body")
    except _UninspectablePartError:
        raise NativeScanEnforcementError(
            _evidence(
                NativeScanOutcome.ENFORCEMENT_UNAVAILABLE,
                reason="uninspectable_binary_part",
            )
        ) from None
    except _UndecodableFieldError:
        raise NativeScanEnforcementError(
            _evidence(
                NativeScanOutcome.ENFORCEMENT_UNAVAILABLE,
                reason="undecodable_field",
            )
        ) from None

    if not items:
        return _evidence(NativeScanOutcome.ALLOW)

    try:
        result = scan_outbound_bundle(items, high_security_mode=True)
    except Exception:  # noqa: BLE001 - a scanner failure must fail closed
        raise NativeScanEnforcementError(
            _evidence(
                NativeScanOutcome.ENFORCEMENT_UNAVAILABLE,
                reason="scanner_error",
            )
        ) from None

    if not result.allowed:
        findings = tuple(
            NativeScanFinding(category=finding.category, location=finding.location)
            for finding in result.findings
        )
        raise NativeScanBlockedError(
            _evidence(NativeScanOutcome.BLOCK, findings=findings)
        )

    return _evidence(NativeScanOutcome.ALLOW)


__all__ = [
    "NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION",
    "NativeScanBlockedError",
    "NativeScanEnforcementError",
    "NativeScanEvidence",
    "NativeScanFinding",
    "NativeScanOutcome",
    "NativeScanSurface",
    "canonical_payload_digest",
    "scan_native_outbound",
]
