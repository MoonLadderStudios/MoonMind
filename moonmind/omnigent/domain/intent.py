"""Execution intent value objects.

An :class:`ExecutionIntent` is the immutable, provider-neutral statement of what
a caller wants a turn/session to do, derived from a request at the adapter
boundary and consumed by application use cases. It carries no transport,
persistence, or provider handles — only the decision-relevant fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """What a caller intends for a session turn, free of infrastructure."""

    bridge_session_id: str
    first_message_digest: str | None = None
    idempotency_key: str | None = None
    require_full_evidence: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def with_metadata(self, **entries: str) -> "ExecutionIntent":
        merged = dict(self.metadata)
        merged.update(entries)
        return ExecutionIntent(
            bridge_session_id=self.bridge_session_id,
            first_message_digest=self.first_message_digest,
            idempotency_key=self.idempotency_key,
            require_full_evidence=self.require_full_evidence,
            metadata=MappingProxyType(merged),
        )


__all__ = ["ExecutionIntent"]
