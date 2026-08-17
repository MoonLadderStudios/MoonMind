"""Independent recorder of provider side effects and idempotency identities.

Owned by MoonLadderStudios/MoonMind#3709.

The recorder is deliberately independent of MoonMind's own reconciler state so
tests can assert at-most-once behavior from evidence the system under test does
not control. It captures, for every logical command:

* the request (logical command, payload digest, logical idempotency key);
* the durable side effect committed by the provider, if any;
* the response returned to the caller (or that it was dropped);
* every observation (event batch, snapshot) surfaced back to the caller.

All retained evidence is scrubbed for secrets so minimized scenarios and
diagnostic bundles contain no raw credentials or production secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.scenario import ResponseMode, SideEffectKind

#: Secret-like patterns that must never appear in retained fault evidence.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"ATATT[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:authorization|password|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
)


def scan_for_secrets(value: Any) -> list[str]:
    """Return descriptions of secret-like content found anywhere in ``value``.

    An empty list means the payload is safe to retain in a fixture or bundle.
    """

    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    found: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(pattern.pattern)
    return found


def payload_digest(payload: Any) -> str:
    """Return a stable, secret-free digest identifying a payload."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RecordedRequest:
    """A logical request the control plane issued to the provider/host."""

    sequence: int
    command: LogicalCommand
    idempotency_key: str | None
    payload_digest: str
    generation: int


@dataclass(frozen=True)
class RecordedSideEffect:
    """A durable side effect the provider committed for a request."""

    sequence: int
    command: LogicalCommand
    kind: SideEffectKind
    idempotency_key: str | None
    generation: int


@dataclass(frozen=True)
class RecordedResponse:
    """The response the caller received (or that it was dropped/failed)."""

    sequence: int
    command: LogicalCommand
    mode: ResponseMode
    delivered: bool


@dataclass(frozen=True)
class RecordedObservation:
    """An observation (event batch or snapshot) surfaced to the caller."""

    sequence: int
    command: LogicalCommand
    kind: str  # "events" | "snapshot"
    frontier: int
    payload_digest: str


@dataclass
class ProviderRecorder:
    """Append-only, secret-scrubbed record of everything the fake provider did."""

    requests: list[RecordedRequest] = field(default_factory=list)
    side_effects: list[RecordedSideEffect] = field(default_factory=list)
    responses: list[RecordedResponse] = field(default_factory=list)
    observations: list[RecordedObservation] = field(default_factory=list)
    _seq: int = 0

    def next_sequence(self) -> int:
        self._seq += 1
        return self._seq

    def record_request(
        self,
        *,
        command: LogicalCommand,
        payload: Any,
        idempotency_key: str | None,
        generation: int,
    ) -> RecordedRequest:
        self._assert_safe(payload)
        req = RecordedRequest(
            sequence=self.next_sequence(),
            command=command,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest(payload),
            generation=generation,
        )
        self.requests.append(req)
        return req

    def record_side_effect(
        self,
        *,
        command: LogicalCommand,
        kind: SideEffectKind,
        idempotency_key: str | None,
        generation: int,
    ) -> RecordedSideEffect:
        eff = RecordedSideEffect(
            sequence=self.next_sequence(),
            command=command,
            kind=kind,
            idempotency_key=idempotency_key,
            generation=generation,
        )
        self.side_effects.append(eff)
        return eff

    def record_response(
        self, *, command: LogicalCommand, mode: ResponseMode, delivered: bool
    ) -> None:
        self.responses.append(
            RecordedResponse(
                sequence=self.next_sequence(),
                command=command,
                mode=mode,
                delivered=delivered,
            )
        )

    def record_observation(
        self, *, command: LogicalCommand, kind: str, frontier: int, payload: Any
    ) -> None:
        self._assert_safe(payload)
        self.observations.append(
            RecordedObservation(
                sequence=self.next_sequence(),
                command=command,
                kind=kind,
                frontier=frontier,
                payload_digest=payload_digest(payload),
            )
        )

    # -- independent assertions -------------------------------------------------

    def accepted_side_effect_count(self, idempotency_key: str) -> int:
        """How many *accepted* turn side effects share one idempotency identity."""
        return sum(
            1
            for eff in self.side_effects
            if eff.idempotency_key == idempotency_key
            and eff.kind is SideEffectKind.ACCEPTED
        )

    def side_effect_count(self, kind: SideEffectKind) -> int:
        return sum(1 for eff in self.side_effects if eff.kind is kind)

    def request_count(self, command: LogicalCommand) -> int:
        return sum(1 for req in self.requests if req.command is command)

    def _assert_safe(self, payload: Any) -> None:
        leaks = scan_for_secrets(payload)
        if leaks:
            raise ValueError(
                "refusing to record payload containing secret-like content: "
                f"{leaks}"
            )

    def to_journal(self) -> dict[str, Any]:
        """A compact, secret-free JSON projection for diagnostic bundles."""
        return {
            "requests": [
                {
                    "seq": r.sequence,
                    "command": r.command.value,
                    "idempotencyKey": r.idempotency_key,
                    "payloadDigest": r.payload_digest,
                    "generation": r.generation,
                }
                for r in self.requests
            ],
            "sideEffects": [
                {
                    "seq": e.sequence,
                    "command": e.command.value,
                    "kind": e.kind.value,
                    "idempotencyKey": e.idempotency_key,
                    "generation": e.generation,
                }
                for e in self.side_effects
            ],
            "responses": [
                {
                    "seq": r.sequence,
                    "command": r.command.value,
                    "mode": r.mode.value,
                    "delivered": r.delivered,
                }
                for r in self.responses
            ],
            "observations": [
                {
                    "seq": o.sequence,
                    "command": o.command.value,
                    "kind": o.kind,
                    "frontier": o.frontier,
                    "payloadDigest": o.payload_digest,
                }
                for o in self.observations
            ],
        }


__all__ = [
    "ProviderRecorder",
    "RecordedRequest",
    "RecordedSideEffect",
    "RecordedResponse",
    "RecordedObservation",
    "payload_digest",
    "scan_for_secrets",
]
