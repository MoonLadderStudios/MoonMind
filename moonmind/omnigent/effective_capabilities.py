"""Effective native capability authority for MoonLadderStudios/MoonMind#3636.

The browser manifest and request enforcement consume the same immutable result.
This module is deliberately runtime-neutral: callers provide an execution-bound
authority snapshot, upstream evidence, current state, and caller authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping

CAPABILITY_SCHEMA_VERSION = "moonmind.omnigent.effective-capabilities.v1"

CAPABILITY_NAMES: tuple[str, ...] = (
    "viewTranscript",
    "sendMessage",
    "queueMessage",
    "interruptTurn",
    "resolveElicitation",
    "stopSession",
    "replaceSession",
    "readResources",
    "uploadFiles",
    "mutateWorkspace",
    "createTerminal",
    "attachTerminal",
    "viewTerminal",
    "writeTerminal",
    "closeTerminal",
    "openBrowser",
    "viewSubagents",
    "controlSubagents",
    "changeModel",
    "changeEffort",
    "changeGoal",
    "reconnectSession",
    "harvestEvidence",
    "cleanupSession",
)

MUTATION_CAPABILITIES = frozenset(CAPABILITY_NAMES) - frozenset(
    {"viewTranscript", "readResources", "viewTerminal", "viewSubagents"}
)
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "stopped"}
)


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    reason: str | None
    upstream_supported: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "disabledReason": self.reason,
            "upstreamSupported": self.upstream_supported,
        }


@dataclass(frozen=True, slots=True)
class EffectiveCapabilitySet:
    schema_version: str
    authority_digest: str
    decisions: Mapping[str, CapabilityDecision]

    @property
    def capabilities(self) -> dict[str, bool]:
        return {name: decision.allowed for name, decision in self.decisions.items()}

    @property
    def disabled_reasons(self) -> dict[str, str]:
        return {
            name: decision.reason
            for name, decision in self.decisions.items()
            if not decision.allowed and decision.reason is not None
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "authorityDigest": self.authority_digest,
            "capabilities": self.capabilities,
            "disabledReasons": self.disabled_reasons,
            "decisions": {
                name: decision.as_dict() for name, decision in self.decisions.items()
            },
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _authority_reason(authority: Mapping[str, Any]) -> str | None:
    required = (
        "agentProfileRef", "agentProfileDigest", "providerProfileId",
        "providerProfileGeneration", "launchPolicyRef", "policySnapshotRef",
        "policyDigest", "effectiveLaunchSnapshotRef", "sessionEpoch",
    )
    if any(not _text(authority.get(key)) for key in required):
        return "immutable_authority_missing"
    expected_generation = authority.get("expectedProviderProfileGeneration")
    if expected_generation is not None and str(expected_generation) != str(
        authority.get("providerProfileGeneration")
    ):
        return "provider_generation_stale"
    expected_epoch = authority.get("expectedSessionEpoch")
    if expected_epoch is not None and str(expected_epoch) != str(
        authority.get("sessionEpoch")
    ):
        return "session_epoch_stale"
    if authority.get("authorityFresh") is not True:
        return "immutable_authority_stale"
    return None


def resolve_effective_capabilities(
    *,
    authority: Mapping[str, Any],
    upstream_capabilities: Mapping[str, Any],
    profile_capabilities: Mapping[str, Any],
    launch_capabilities: Mapping[str, Any],
    state_capabilities: Mapping[str, Any],
    caller_capabilities: Mapping[str, Any],
    session_status: str | None,
) -> EffectiveCapabilitySet:
    """Intersect all five authorities and return stable fail-closed decisions."""

    authority_reason = _authority_reason(authority)
    terminal = _text(session_status).lower() in TERMINAL_STATUSES
    decisions: dict[str, CapabilityDecision] = {}
    sources = (
        (upstream_capabilities, "upstream_unsupported"),
        (profile_capabilities, "agent_profile_denied"),
        (launch_capabilities, "launch_policy_denied"),
        (state_capabilities, "session_state_denied"),
        (caller_capabilities, "caller_not_authorized"),
    )
    for name in CAPABILITY_NAMES:
        upstream_supported = upstream_capabilities.get(name) is True
        reason = authority_reason
        if reason is None and terminal and name in MUTATION_CAPABILITIES:
            reason = "session_terminal"
        if reason is None:
            for source, denied_reason in sources:
                # Every source must explicitly grant. Missing and non-boolean
                # values fail closed; no mutable/default inference is allowed.
                if source.get(name) is not True:
                    reason = denied_reason
                    break
        decisions[name] = CapabilityDecision(
            allowed=reason is None,
            reason=reason,
            upstream_supported=upstream_supported,
        )

    digest_payload = {
        "schemaVersion": CAPABILITY_SCHEMA_VERSION,
        "authority": dict(authority),
        "status": _text(session_status).lower(),
        "decisions": {name: item.as_dict() for name, item in decisions.items()},
    }
    digest = sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EffectiveCapabilitySet(CAPABILITY_SCHEMA_VERSION, digest, decisions)
