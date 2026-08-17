"""Provider-native status vocabulary translation.

The canonical MoonMind status vocabulary uses ``canceled`` and ``timed_out``.
Providers emit native aliases (``cancelled``, ``timeout``). Translation of those
aliases into canonical values is a domain concern so every layer shares one
mapping instead of re-deriving it. This is the pure half of the "eliminate
duplicate provider vocabulary" work in the issue; adapters translate richer
provider payloads into canonical observations and reuse this helper for status
strings.
"""

from __future__ import annotations

# Provider-native aliases normalized before coalescence.
PROVIDER_STATUS_ALIASES: dict[str, str] = {
    "cancelled": "canceled",
    "timeout": "timed_out",
}


def canonicalize_provider_status(value: str) -> str:
    """Return the canonical, lower-cased status for a provider-native value."""

    raw = str(value).strip().lower()
    return PROVIDER_STATUS_ALIASES.get(raw, raw)


__all__ = ["PROVIDER_STATUS_ALIASES", "canonicalize_provider_status"]
