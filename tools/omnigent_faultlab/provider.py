"""Programmable fake Omnigent provider with an independent recording ledger.

Source issue: MoonLadderStudios/MoonMind#3709.

The fake models the *provider boundary* only. Crucially it records every
request, logical idempotency key, payload digest, side effect, response, and
observation in a ledger that is independent of MoonMind's durable state, so tests
can assert at-most-once behavior by inspecting the ledger directly rather than
trusting the state the reconciler wrote.

The fake never stores raw payloads or credentials: it stores a salted-free
SHA-256 digest and bounded enum-ish fields only, which keeps retained fault
evidence secret-safe (acceptance criterion "diagnostic artifacts contain no raw
credentials").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .scenario import LogicalOperation, ResponseBehavior, SideEffect


def payload_digest(payload: Any) -> str:
    """Return a stable, secret-free digest of a logical payload.

    Only the digest is ever retained, never the payload, so a scenario that
    carries a prompt or credential-shaped string cannot leak it into the ledger
    or a minimized fixture.
    """

    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RequestRecord:
    """One request the fake received, with its idempotency identity."""

    operation: LogicalOperation
    idempotency_key: str
    payload_digest: str
    response: ResponseBehavior


@dataclass(frozen=True)
class SideEffectRecord:
    """One durable side effect the fake actually performed."""

    operation: LogicalOperation
    idempotency_key: str
    payload_digest: str
    side_effect: SideEffect


@dataclass(frozen=True)
class ObservationRecord:
    """One observation the fake surfaced to the caller."""

    operation: LogicalOperation
    raw_status: str | None
    delivered: bool


@dataclass(frozen=True)
class ProviderResponse:
    """The bounded result of a provider call.

    ``delivered`` is ``False`` when a transport fault (drop/timeout/disconnect)
    lost the response even though the server-side side effect may have happened.
    ``duplicate`` is ``True`` when the idempotency key was already accepted, so no
    second side effect occurred.
    """

    accepted: bool
    delivered: bool
    duplicate: bool
    side_effect: SideEffect
    conflict: bool = False


class SideEffectLedger:
    """Independent, append-only record of provider side effects.

    The ledger is what makes at-most-once assertable without trusting MoonMind:
    a logical idempotency key can back at most one performed side effect no matter
    how many times, or across how many crash windows, the command is retried.
    """

    def __init__(self) -> None:
        self.requests: list[RequestRecord] = []
        self.side_effects: list[SideEffectRecord] = []
        self.observations: list[ObservationRecord] = []
        self._first_digest_by_key: dict[str, str] = {}
        self._accepted_keys: set[str] = set()

    def record_request(self, record: RequestRecord) -> None:
        self.requests.append(record)

    def record_observation(self, record: ObservationRecord) -> None:
        self.observations.append(record)

    def apply_side_effect(
        self,
        operation: LogicalOperation,
        idempotency_key: str,
        digest: str,
        side_effect: SideEffect,
    ) -> tuple[bool, bool]:
        """Apply a side effect under an idempotency key.

        Returns ``(performed, conflict)``. ``performed`` is ``True`` only the
        first time a key is accepted; subsequent applications are deduped (no
        second side effect). ``conflict`` reports a key reused with a different
        payload digest, which a correct caller never does.
        """

        conflict = (
            idempotency_key in self._first_digest_by_key
            and self._first_digest_by_key[idempotency_key] != digest
        )
        self._first_digest_by_key.setdefault(idempotency_key, digest)
        if idempotency_key in self._accepted_keys:
            return False, conflict
        self._accepted_keys.add(idempotency_key)
        self.side_effects.append(
            SideEffectRecord(operation, idempotency_key, digest, side_effect)
        )
        return True, conflict

    def accepted_side_effect_count(self, idempotency_key: str) -> int:
        """How many side effects a key actually performed (must never exceed 1)."""

        return sum(
            1 for rec in self.side_effects if rec.idempotency_key == idempotency_key
        )

    def keys_with_multiple_side_effects(self) -> list[str]:
        """Idempotency keys that performed more than one side effect (a bug)."""

        counts: dict[str, int] = {}
        for rec in self.side_effects:
            counts[rec.idempotency_key] = counts.get(rec.idempotency_key, 0) + 1
        return sorted(key for key, count in counts.items() if count > 1)


class ProgrammableFakeProvider:
    """Deterministic, scenario-driven fake provider.

    The provider does not itself decide lifecycle policy; it applies scripted
    fault behavior to each call and records everything in its ledger. The harness
    supplies the idempotency key (the reducer's ``command_id``) and a payload, so
    the ledger keys line up with MoonMind's own logical command identity while
    remaining an independent witness.
    """

    def __init__(self) -> None:
        self.ledger = SideEffectLedger()

    def call(
        self,
        operation: LogicalOperation,
        *,
        idempotency_key: str,
        payload: Any,
        side_effect: SideEffect,
        response: ResponseBehavior = ResponseBehavior.SUCCESS,
    ) -> ProviderResponse:
        """Perform one logical provider call under a scripted response behavior."""

        digest = payload_digest(payload)
        self.ledger.record_request(
            RequestRecord(operation, idempotency_key, digest, response)
        )

        performed = False
        conflict = False
        if side_effect != SideEffect.NONE:
            performed, conflict = self.ledger.apply_side_effect(
                operation, idempotency_key, digest, side_effect
            )

        # Transport faults lose the response even when the side effect happened.
        delivered = response == ResponseBehavior.SUCCESS
        accepted = side_effect != SideEffect.NONE
        return ProviderResponse(
            accepted=accepted,
            delivered=delivered,
            duplicate=accepted and not performed,
            side_effect=side_effect,
            conflict=conflict,
        )

    def observe(
        self,
        operation: LogicalOperation,
        *,
        raw_status: str | None,
        delivered: bool = True,
    ) -> None:
        """Record an observation the fake surfaced (for the request log)."""

        self.ledger.record_observation(
            ObservationRecord(operation, raw_status, delivered)
        )


__all__ = [
    "payload_digest",
    "RequestRecord",
    "SideEffectRecord",
    "ObservationRecord",
    "ProviderResponse",
    "SideEffectLedger",
    "ProgrammableFakeProvider",
]
