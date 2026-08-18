"""Secret-safe diagnostic bundles for fault-lab failures.

Source issue: MoonLadderStudios/MoonMind#3709.

When a generated run violates an invariant, the suite emits a diagnostic bundle
that is sufficient to reproduce the failure with no credentials, network access,
or raw production logs (acceptance criterion). The bundle carries the seed, the
minimized declarative scenario, the decision journal, the provider request log,
and the invariant violations — all of it bounded enum/digest data, never raw
payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .conversions import plan_to_scenario
from .harness import ExecutionTrace
from .invariants import violations as invariant_violations

# Patterns that would indicate a leaked secret in an outgoing bundle. Mirrors the
# repo's security guardrail set so a bundle can never carry a live credential.
_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+"),
)


class SecretLeakError(AssertionError):
    """Raised when a diagnostic bundle would contain a secret-shaped value."""


@dataclass
class DiagnosticBundle:
    """A bounded, reviewable, reproduction-complete failure record."""

    seed: int
    scenario: dict[str, Any]
    invariant_violations: list[str]
    decision_journal: list[dict[str, Any]]
    provider_request_log: list[dict[str, Any]]
    side_effect_summary: dict[str, int]
    safe_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "scenario": self.scenario,
            "invariantViolations": self.invariant_violations,
            "decisionJournal": self.decision_journal,
            "providerRequestLog": self.provider_request_log,
            "sideEffectSummary": self.side_effect_summary,
            "safeRefs": self.safe_refs,
        }


def _assert_no_secrets(payload: Any) -> None:
    """Fail closed if any nested string matches a secret pattern (invariant 11)."""

    def walk(node: Any) -> None:
        if isinstance(node, str):
            for pattern in _SECRET_PATTERNS:
                if pattern.search(node):
                    raise SecretLeakError("diagnostic bundle would leak a secret")
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(payload)


def build_diagnostic_bundle(
    trace: ExecutionTrace,
    *,
    scenario_id: str | None = None,
    source_ref: str | None = None,
    invariant: str | None = None,
) -> DiagnosticBundle:
    """Assemble a reproduction-complete, secret-safe bundle for a trace."""

    scenario = plan_to_scenario(
        trace.plan,
        scenario_id=scenario_id,
        source_ref=source_ref,
        invariant=invariant,
    ).to_wire()

    decision_journal = [
        {
            "round": e.round_index,
            "kind": e.decision_kind.value,
            "reason": e.reason_code,
            "commandId": e.command_id,
            "fenced": e.fenced,
            "crashWindow": e.crash_window.value if e.crash_window else None,
        }
        for e in trace.journal
    ]

    provider_request_log = [
        {
            "operation": rec.operation.value,
            "idempotencyKey": rec.idempotency_key,
            "payloadDigest": rec.payload_digest,
            "response": rec.response.value,
        }
        for rec in trace.ledger.requests
    ]

    side_effect_summary: dict[str, int] = {}
    for rec in trace.ledger.side_effects:
        side_effect_summary[rec.operation.value] = (
            side_effect_summary.get(rec.operation.value, 0) + 1
        )

    bundle = DiagnosticBundle(
        seed=trace.plan.seed,
        scenario=scenario,
        invariant_violations=invariant_violations(trace),
        decision_journal=decision_journal,
        provider_request_log=provider_request_log,
        side_effect_summary=side_effect_summary,
        safe_refs={
            "convergence": "converged" if trace.converged else "not_converged",
            "finalTerminal": str(trace.final.terminal_outcome),
        },
    )
    _assert_no_secrets(bundle.to_dict())
    return bundle


__all__ = ["DiagnosticBundle", "SecretLeakError", "build_diagnostic_bundle"]
