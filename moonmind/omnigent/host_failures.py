"""Pure Omnigent host failure vocabulary.

Source issue: MoonLadderStudios/MoonMind#3711.

The host failure vocabulary is a pure contract: decision code, application
coordination, and adapters all raise and classify the same authority error
without importing persistence, provider transport, or framework modules. The
durable persistence boundary (``moonmind.omnigent.oauth_hosts``) consumes this
vocabulary; it does not own it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.utils.logging import redact_sensitive_text


class OmnigentOAuthHostError(RuntimeError):
    code = "OMNIGENT_OAUTH_HOST_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        egress_evidence_ref: str | None = None,
        cleanup_evidence: Mapping[str, Any] | None = None,
        prepared_host_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(redact_sensitive_text(message)[:512])
        if code:
            self.code = code
        # Cleanup can fail after protected terminal evidence has already been
        # published.  Keep that objective evidence attached to the raised
        # authority error so the coordinator or janitor can durably project it
        # instead of reducing the outcome to exception prose.
        self.egress_evidence_ref = str(egress_evidence_ref or "").strip() or None
        self.cleanup_evidence = dict(cleanup_evidence or {})
        # A host may already be attached to its enforced network when protected
        # launch-evidence publication or durable authority binding fails. Carry
        # the bounded, non-secret runtime result to the coordinator so cleanup
        # can still publish objective terminal evidence before capacity release.
        # This is process-local handoff evidence, never a replacement for the
        # bridge store's durable authority used by a later janitor.
        self.prepared_host_evidence = dict(prepared_host_evidence or {})


__all__ = ["OmnigentOAuthHostError"]
