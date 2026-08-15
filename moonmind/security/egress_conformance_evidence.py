"""Versioned, independently-resolvable per-row egress conformance evidence.

MoonLadderStudios/MoonMind#3625.  The restricted-egress substrate (#3516) proves
enforcement at launch and cleanup and publishes attestation/lifecycle evidence.
This module makes that durable evidence tamper-evident and secret-clean so it can
survive the invariants of the security gate:

* **Independently resolvable after cleanup.**  Each artifact carries a content
  digest (``evidenceDigest``) computed over its own body.  A resolver that reads
  the artifact back — after the live host or helper is gone — can recompute the
  digest and prove the evidence was neither truncated nor rewritten.
* **Secret-clean.**  The serialized evidence is scanned with the shared outbound
  secret scanner forced into high-security mode before it is published.  A
  finding fails closed rather than persisting a credential into durable evidence.

The primitives here operate on the plain evidence dicts the trusted backends
already build, so making production evidence tamper-evident is additive: no
existing field changes shape and in-flight consumers keep reading the same keys.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any

from moonmind.security.outbound_scan import scan_outbound_text

# The digest is bound into the artifact under this key.  It is deliberately
# excluded from its own computation so a resolver can recompute the body digest
# and compare it with the stored value.
EGRESS_EVIDENCE_DIGEST_KEY = "evidenceDigest"

EvidencePublisher = Callable[[Any, str, bytes], Awaitable[str]]


class EgressEvidenceSecretError(RuntimeError):
    """Raised when egress conformance evidence carries secret-like content."""


class EgressEvidenceDigestError(RuntimeError):
    """Raised when a resolved evidence body does not match its bound digest."""


def _canonical_body(payload: Mapping[str, Any]) -> bytes:
    """Serialize the evidence body deterministically, excluding the digest key."""

    body = {
        key: value
        for key, value in payload.items()
        if key != EGRESS_EVIDENCE_DIGEST_KEY
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_content_digest(payload: Mapping[str, Any]) -> str:
    """Return the ``sha256:`` digest of the evidence body sans its digest key."""

    return "sha256:" + hashlib.sha256(_canonical_body(payload)).hexdigest()


def attach_evidence_digest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with a recomputed content digest bound in."""

    bound = deepcopy(dict(payload))
    bound[EGRESS_EVIDENCE_DIGEST_KEY] = evidence_content_digest(payload)
    return bound


def verify_evidence_digest(payload: Mapping[str, Any]) -> None:
    """Fail closed unless the bound digest matches the resolved body.

    A missing digest is treated as tampering: unversioned evidence cannot be
    accepted as independently resolvable.
    """

    stored = payload.get(EGRESS_EVIDENCE_DIGEST_KEY)
    if not isinstance(stored, str) or not stored:
        raise EgressEvidenceDigestError(
            "egress conformance evidence is missing its content digest"
        )
    recomputed = evidence_content_digest(payload)
    if stored != recomputed:
        raise EgressEvidenceDigestError(
            "egress conformance evidence digest does not match the resolved body"
        )


def secret_scan_evidence(payload: Mapping[str, Any], *, location: str) -> None:
    """Fail closed if the serialized evidence contains secret-like content.

    The scan is forced into high-security mode so evidence is held to the strict
    outbound contract regardless of the deployment's runtime security setting.
    """

    serialized = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    )
    result = scan_outbound_text(
        serialized,
        location=location,
        high_security_mode=True,
    )
    if not result.allowed:
        raise EgressEvidenceSecretError(
            "egress conformance evidence failed the outbound secret scan: "
            + "; ".join(result.sanitized_diagnostics)
        )


def serialize_conformance_evidence(
    payload: Mapping[str, Any], *, location: str
) -> bytes:
    """Bind a content digest, secret-scan, and return canonical evidence bytes."""

    bound = attach_evidence_digest(payload)
    secret_scan_evidence(bound, location=location)
    return json.dumps(bound, sort_keys=True, separators=(",", ":")).encode("utf-8")


async def publish_conformance_evidence(
    request: Any,
    name: str,
    payload: Mapping[str, Any],
    *,
    publisher: EvidencePublisher,
    location: str | None = None,
) -> str:
    """Digest-bind, secret-scan, and publish one versioned evidence artifact."""

    data = serialize_conformance_evidence(payload, location=location or name)
    return await publisher(request, name, data)


def parse_and_verify_conformance_evidence(
    resolved: bytes | str | Mapping[str, Any], *, location: str
) -> dict[str, Any]:
    """Verify a resolved artifact is digest-consistent and secret-clean.

    Accepts the raw bytes/text a resolver returns after cleanup, or the already
    decoded mapping.  Raises when the body does not match its bound digest or
    when the evidence carries secret-like content.
    """

    if isinstance(resolved, Mapping):
        payload: dict[str, Any] = dict(resolved)
    else:
        text = resolved.decode("utf-8") if isinstance(resolved, bytes) else str(resolved)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EgressEvidenceDigestError(
                "resolved egress conformance evidence is not valid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise EgressEvidenceDigestError(
                "resolved egress conformance evidence is not an object"
            )
        payload = decoded
    verify_evidence_digest(payload)
    secret_scan_evidence(payload, location=location)
    return payload
