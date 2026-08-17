"""Pure identifier value objects and derivation for the Omnigent domain.

Identifier derivation is a domain concern: how a bridge session id, chat-binding
id, or event deduplication key is *shaped* must not depend on persistence,
transport, or framework details. Generation of unguessable tokens is a
side-effect owned by adapters; the domain owns the shape, prefixes, and bounded
normalization so every layer agrees on identity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

CHAT_BINDING_ID_PREFIX = "chatb_"

# Maximum length of a durable bridge-event deduplication key. Longer keys are
# folded to a bounded, collision-resistant form that preserves distinguishing
# suffixes (mirrors ``docs/Omnigent/OmnigentBridge.md`` §10 identity rules).
EVENT_DEDUPLICATION_KEY_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Canonical identity of a bridge session.

    ``bridge_session_id`` is MoonMind-owned and always present. The provider's
    ``omnigent_session_id`` is present only once the session is provider-bound;
    ``is_provider_bound`` gates transitions that require a provider session.
    """

    bridge_session_id: str
    omnigent_session_id: str | None = None

    @property
    def is_provider_bound(self) -> bool:
        return bool((self.omnigent_session_id or "").strip())


def bounded_deduplication_key(
    key: str,
    *,
    max_length: int = EVENT_DEDUPLICATION_KEY_MAX_LENGTH,
) -> str:
    """Fit a durable event identity without discarding distinguishing suffixes."""

    if len(key) <= max_length:
        return key
    digest = hashlib.sha256(key.encode()).hexdigest()
    prefix_length = max_length - len(digest) - 1
    return f"{key[:prefix_length]}:{digest}"


def is_chat_binding_id(value: str | None) -> bool:
    """Return whether a value has the canonical chat-binding id shape."""

    return bool(value) and str(value).startswith(CHAT_BINDING_ID_PREFIX)


__all__ = [
    "CHAT_BINDING_ID_PREFIX",
    "EVENT_DEDUPLICATION_KEY_MAX_LENGTH",
    "SessionRef",
    "bounded_deduplication_key",
    "is_chat_binding_id",
]
