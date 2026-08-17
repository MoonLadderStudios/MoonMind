"""Transport policy for WebSocket relay.

Pure decision about whether a WebSocket upgrade may be relayed to the provider
and under which subprotocol. Executes no I/O; the router owns the actual relay.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RelayDecision:
    relay: bool
    reason: str
    subprotocol: str | None = None


def decide_relay(*, authorized: bool, session_active: bool) -> RelayDecision:
    if not authorized:
        return RelayDecision(relay=False, reason="unauthorized")
    if not session_active:
        return RelayDecision(relay=False, reason="session_not_active")
    return RelayDecision(relay=True, reason="relay", subprotocol="omnigent.bridge.v1")


__all__ = ["RelayDecision", "decide_relay"]
