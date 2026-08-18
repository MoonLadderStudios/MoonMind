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

from moonmind.schemas.omnigent_execution_intent import EXECUTION_INTENT_SCHEMA

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

# Provider facades report their own operation names.  This is the sole scoped
# translation boundary into MoonMind's canonical capability namespace; the
# resolver itself never guesses aliases or treats an unknown provider bit as
# authority.
PROVIDER_CAPABILITY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "sendFollowUp": ("sendMessage",),
    "stop": ("stopSession",),
    "harvest": ("harvestEvidence",),
    "clearSession": ("replaceSession",),
    "terminalCleanup": ("cleanupSession",),
    "newSession": ("replaceSession", "reconnectSession"),
    "interrupt": ("interruptTurn",),
}

_PROVIDER_PRESENTATION_CAPABILITIES = frozenset(
    {"viewTranscript", "readResources", "viewTerminal", "viewSubagents"}
)

_OWNER_CAPABILITIES = frozenset(CAPABILITY_NAMES) - frozenset(
    {"resolveElicitation", "harvestEvidence", "cleanupSession"}
)


def adapt_provider_capabilities(capabilities: Mapping[str, Any]) -> dict[str, bool]:
    """Adapt bounded provider evidence to the complete canonical namespace."""

    # Intervention capability maps describe provider controls, not the complete
    # presentation surface. The binding facade owns these read projections and
    # the pinned compatibility contract proves their support independently.
    adapted = {
        name: name in _PROVIDER_PRESENTATION_CAPABILITIES for name in CAPABILITY_NAMES
    }
    for name in CAPABILITY_NAMES:
        if capabilities.get(name) is True:
            adapted[name] = True
    for provider_name, canonical_names in PROVIDER_CAPABILITY_ALIASES.items():
        if capabilities.get(provider_name) is True:
            for canonical_name in canonical_names:
                adapted[canonical_name] = True
    return adapted


MUTATION_CAPABILITIES = frozenset(CAPABILITY_NAMES) - frozenset(
    {"viewTranscript", "readResources", "viewTerminal", "viewSubagents"}
)
# Evidence harvest and cleanup are post-terminal lifecycle operations. They are
# still gated by every authority source and operation-specific durable state;
# terminal status alone must not make them unreachable.
POST_TERMINAL_MUTATIONS = frozenset({"harvestEvidence", "cleanupSession"})
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
        "agentProfileRef",
        "agentProfileDigest",
        "providerProfileId",
        "providerProfileGeneration",
        "launchPolicyRef",
        "policySnapshotRef",
        "policyDigest",
        "effectiveLaunchSnapshotRef",
        "sessionEpoch",
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
        if (
            reason is None
            and terminal
            and name in MUTATION_CAPABILITIES
            and name not in POST_TERMINAL_MUTATIONS
        ):
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


def resolve_bridge_row_capabilities(
    row: Any,
    *,
    caller_capabilities: Mapping[str, Any],
    expected_session_epoch: Any = None,
    expected_active_turn: Any = None,
    expected_elicitation: Any = None,
    expected_agent_profile_digest: Any = None,
    expected_provider_generation: Any = None,
    expected_launch_snapshot_ref: Any = None,
    expected_policy_digest: Any = None,
) -> EffectiveCapabilitySet:
    """Resolve the canonical capability set from one durable bridge row.

    ``capabilityAuthority`` is written with the execution-bound bridge
    projection and is the sole capability input.  The adapter deliberately
    does not infer grants from provider names, mutable defaults, or the legacy
    ``interventionCapabilities`` map.  Expected request fields are compared at
    this boundary so the browser manifest and mutation handoff use identical
    authority semantics.
    """

    metadata = dict(getattr(row, "metadata_", None) or {})
    evidence = dict(metadata.get("capabilityAuthority") or {})
    intent_requirement = str(
        metadata.get("executionIntentRequirement") or "legacy_history"
    ).strip()
    compiled_binding = dict(metadata.get("compiledExecutionIntent") or {})
    compiled_view = dict(compiled_binding.get("runtimeView") or {})
    launch = dict(getattr(row, "effective_launch_snapshot_json", None) or {})
    policy = dict(launch.get("policyAuthority") or {})
    state = dict(evidence.get("state") or {})
    authority = {
        "agentProfileRef": launch.get("executionProfileRef"),
        "agentProfileDigest": launch.get("executionProfileDigest"),
        "providerProfileId": getattr(row, "provider_profile_id", None),
        "providerProfileGeneration": getattr(row, "credential_generation", None),
        "launchPolicyRef": launch.get("launchPolicyRef"),
        "policySnapshotRef": policy.get("snapshotRef"),
        "policyDigest": policy.get("policyDigest"),
        "effectiveLaunchSnapshotRef": launch.get("snapshotRef"),
        "sessionEpoch": state.get("sessionEpoch"),
        "authorityFresh": evidence.get("fresh") is True,
        "expectedProviderProfileGeneration": evidence.get("providerProfileGeneration"),
        "expectedSessionEpoch": expected_session_epoch,
        "compiledExecutionIntentRef": compiled_binding.get("artifactRef"),
        "compiledExecutionIntentDigest": compiled_binding.get("intentDigest"),
    }
    stale_reason = None
    if intent_requirement == "required" and not compiled_binding:
        stale_reason = "compiled_execution_intent_missing"
    elif compiled_binding:
        required_binding = (
            "artifactRef",
            "intentDigest",
            "intentSchema",
            "runtimeView",
        )
        if any(not compiled_binding.get(key) for key in required_binding):
            stale_reason = "compiled_execution_intent_incomplete"
        elif (
            compiled_binding.get("intentSchema") != EXECUTION_INTENT_SCHEMA
            or compiled_view.get("schema") != EXECUTION_INTENT_SCHEMA
        ):
            stale_reason = "compiled_execution_intent_schema_unsupported"
        elif compiled_view.get("intentDigest") != compiled_binding.get(
            "intentDigest"
        ):
            stale_reason = "compiled_execution_intent_digest_stale"
        elif compiled_view.get("claimsFullAuthority") is not True:
            stale_reason = "compiled_execution_intent_authority_unproven"
        else:
            compiled_preconditions = (
                (
                    compiled_view.get("agentProfileDigest"),
                    launch.get("executionProfileDigest"),
                    "compiled_execution_intent_profile_stale",
                ),
                (
                    compiled_view.get("providerProfileId"),
                    getattr(row, "provider_profile_id", None),
                    "compiled_execution_intent_provider_stale",
                ),
                (
                    compiled_view.get("credentialGeneration"),
                    getattr(row, "credential_generation", None),
                    "compiled_execution_intent_generation_stale",
                ),
                (
                    compiled_view.get("effectiveLaunchSnapshotRef"),
                    launch.get("snapshotRef"),
                    "compiled_execution_intent_launch_stale",
                ),
                (
                    compiled_view.get("launchPolicyDigest"),
                    policy.get("policyDigest"),
                    "compiled_execution_intent_policy_stale",
                ),
            )
            for expected, actual, reason in compiled_preconditions:
                if expected is None or str(expected) != str(actual):
                    stale_reason = reason
                    break
    immutable_preconditions = (
        (
            expected_agent_profile_digest,
            launch.get("executionProfileDigest"),
            "agent_profile_stale",
        ),
        (
            expected_provider_generation,
            getattr(row, "credential_generation", None),
            "provider_generation_stale",
        ),
        (
            expected_launch_snapshot_ref,
            launch.get("snapshotRef"),
            "launch_snapshot_stale",
        ),
        (
            expected_policy_digest,
            policy.get("policyDigest"),
            "policy_snapshot_stale",
        ),
    )
    for expected, actual, reason in immutable_preconditions:
        if stale_reason:
            break
        if expected is not None and str(expected) != str(actual):
            stale_reason = reason
            break
    if expected_active_turn is not None and str(expected_active_turn) != str(
        state.get("activeTurnId")
    ):
        stale_reason = "active_turn_stale"
    if expected_elicitation is not None and str(expected_elicitation) != str(
        state.get("elicitationId")
    ):
        stale_reason = "elicitation_stale"
    if stale_reason:
        authority["authorityFresh"] = False

    result = resolve_effective_capabilities(
        authority=authority,
        upstream_capabilities=dict(evidence.get("upstream") or {}),
        profile_capabilities=dict(evidence.get("agentProfile") or {}),
        launch_capabilities=dict(evidence.get("launchPolicy") or {}),
        state_capabilities=dict(state.get("capabilities") or {}),
        caller_capabilities=caller_capabilities,
        session_status=getattr(row, "status", None),
    )
    if stale_reason:
        decisions = {
            name: CapabilityDecision(False, stale_reason, item.upstream_supported)
            for name, item in result.decisions.items()
        }
        return EffectiveCapabilitySet(
            result.schema_version, result.authority_digest, decisions
        )
    return result


def caller_capabilities_for_bridge(row: Any, caller: Any) -> dict[str, bool]:
    """Resolve caller grants from durable sharing/approval authority.

    Workflow ownership authorizes discovery of a binding, but it does not by
    itself confer approval or administrative lifecycle authority.  Explicit
    per-principal grants are stored on the binding; absent such a grant an
    owner receives the ordinary presentation-client controls only.  Superusers
    retain approval/lifecycle authority through their authenticated role.
    """

    metadata = dict(getattr(row, "metadata_", None) or {})
    principal = _text(getattr(caller, "id", None))
    authorities = metadata.get("callerAuthorities")
    explicit = authorities.get(principal) if isinstance(authorities, Mapping) else None
    if isinstance(explicit, Mapping):
        return {name: explicit.get(name) is True for name in CAPABILITY_NAMES}

    if bool(getattr(caller, "is_superuser", False)):
        return {name: True for name in CAPABILITY_NAMES}

    # The execution owner receives the ordinary presentation-client controls
    # needed for a functional chat session. Approval and post-terminal lifecycle
    # authority remain explicit/superuser-only; the other immutable provider,
    # profile, launch-policy, and state layers still have to grant every action.
    return {name: name in _OWNER_CAPABILITIES for name in CAPABILITY_NAMES}
