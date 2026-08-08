"""Single versioned effective-capability resolver for native Omnigent chat.

Source design: ``docs/UI/WorkflowChatPanel.md`` §5/§7 and
``docs/Omnigent/OmnigentBridge.md`` §7.1/§7.3/§11.

Issue MoonLadderStudios/MoonMind#3636. The native Omnigent application is a
presentation client, never a second control plane. This module is the *one*
authority that derives the effective capability set:

    upstream agent/session capability
    ∩ immutable Omnigent Agent Profile snapshot
    ∩ Provider Profile and effective launch policy
    ∩ Workflow / Step / bridge-session state
    ∩ caller permission and approval authority

The same resolver produces both the browser-safe capability manifest (with
stable per-capability disabled reasons) and the per-request server decision.
Client-side visibility is never authoritative: every mutating request must be
re-checked server-side through :meth:`EffectiveCapabilities.require`.

Resolution uses only execution-bound immutable authority (the exact launch
policy snapshot digest, Provider Profile generation, and — when available — the
Agent Profile digest/version), current session/turn/elicitation state, verified
upstream capability evidence, and the caller's role. It never trusts mutable
profile defaults, provider display-name guesses, browser-supplied capability
snapshots, or chat prose. Stale or missing immutable authority fails closed with
a bounded reason (:func:`resolve_effective_capabilities` returns an all-denied
manifest carrying :attr:`EffectiveCapabilities.fail_closed_reason`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Bump when the manifest key set or decision semantics change so the browser and
# any persisted receipt can detect an incompatible projection.
CAPABILITY_SCHEMA_VERSION = 1

# Immutable launch-policy control-capability vocabulary
# (``profile_bound_execution._compile_persisted_effective_launch``).
_POLICY_INTERRUPT = "interrupt"
_POLICY_TERMINATE = "terminate"
_POLICY_CLEAR = "clear_context"


class NativeCapability(StrEnum):
    """Every native Chat/workspace/approval/terminal/browser/lifecycle op.

    The canonical identity of a capability. The browser manifest key is the
    ``camelCase`` spelling in :data:`_MANIFEST_KEYS`; server enforcement uses
    this enum so a manifest rename can never silently widen authority.
    """

    VIEW_TRANSCRIPT = "view_transcript"
    SEND_MESSAGE = "send_message"
    QUEUE_MESSAGE = "queue_message"
    INTERRUPT_TURN = "interrupt_turn"
    RESOLVE_ELICITATION = "resolve_elicitation"
    STOP_SESSION = "stop_session"
    CLEAR_SESSION = "clear_session"
    READ_RESOURCES = "read_resources"
    UPLOAD_FILES = "upload_files"
    MUTATE_WORKSPACE = "mutate_workspace"
    CREATE_TERMINAL = "create_terminal"
    WRITE_TERMINAL = "write_terminal"
    VIEW_TERMINAL = "view_terminal"
    CLOSE_TERMINAL = "close_terminal"
    OPEN_BROWSER = "open_browser"
    VIEW_SUB_AGENTS = "view_sub_agents"
    CONTROL_SUB_AGENTS = "control_sub_agents"
    CHANGE_MODEL = "change_model"
    CHANGE_EFFORT = "change_effort"
    CHANGE_GOAL = "change_goal"
    RECONNECT_SESSION = "reconnect_session"
    HARVEST_EVIDENCE = "harvest_evidence"
    CLEANUP_SESSION = "cleanup_session"


# Browser-safe manifest keys, mirroring docs/UI/WorkflowChatPanel.md §5.
_MANIFEST_KEYS: dict[NativeCapability, str] = {
    NativeCapability.VIEW_TRANSCRIPT: "viewTranscript",
    NativeCapability.SEND_MESSAGE: "sendMessage",
    NativeCapability.QUEUE_MESSAGE: "queueMessage",
    NativeCapability.INTERRUPT_TURN: "interruptTurn",
    NativeCapability.RESOLVE_ELICITATION: "resolveElicitation",
    NativeCapability.STOP_SESSION: "stopSession",
    NativeCapability.CLEAR_SESSION: "clearSession",
    NativeCapability.READ_RESOURCES: "readResources",
    NativeCapability.UPLOAD_FILES: "uploadFiles",
    NativeCapability.MUTATE_WORKSPACE: "mutateWorkspace",
    NativeCapability.CREATE_TERMINAL: "createTerminal",
    NativeCapability.WRITE_TERMINAL: "writeTerminal",
    NativeCapability.VIEW_TERMINAL: "viewTerminal",
    NativeCapability.CLOSE_TERMINAL: "closeTerminal",
    NativeCapability.OPEN_BROWSER: "openBrowser",
    NativeCapability.VIEW_SUB_AGENTS: "viewSubAgents",
    NativeCapability.CONTROL_SUB_AGENTS: "controlSubAgents",
    NativeCapability.CHANGE_MODEL: "changeModel",
    NativeCapability.CHANGE_EFFORT: "changeEffort",
    NativeCapability.CHANGE_GOAL: "changeGoal",
    NativeCapability.RECONNECT_SESSION: "reconnectSession",
    NativeCapability.HARVEST_EVIDENCE: "harvestEvidence",
    NativeCapability.CLEANUP_SESSION: "cleanupSession",
}


class DisabledReason(StrEnum):
    """Stable, browser-safe reason codes for a denied capability.

    The native UI explains *why* a control is unavailable rather than showing a
    single undifferentiated ``false``. These codes are also the receipt/audit
    ``stableReasonCode`` vocabulary, so they must remain stable strings.
    """

    MISSING_AUTHORITY = "missing_immutable_authority"
    STALE_AUTHORITY = "stale_immutable_authority"
    SESSION_TERMINAL = "session_terminal"
    SESSION_STARTING = "session_starting"
    SESSION_NOT_BOUND = "session_not_provider_bound"
    UNSUPPORTED_UPSTREAM = "unsupported_upstream"
    PINNED_BY_PROFILE = "pinned_by_profile"
    POLICY_FORBIDS = "policy_forbids"
    REQUIRES_MUTATE_AUTHORITY = "requires_mutate_authority"
    REQUIRES_APPROVAL_AUTHORITY = "requires_approval_authority"
    REQUIRES_LIFECYCLE_AUTHORITY = "requires_lifecycle_authority"
    NO_ACTIVE_TURN = "no_active_turn"
    NO_ACTIVE_ELICITATION = "no_active_elicitation"


# Which caller authority a capability requires, beyond mere transcript access.
_AUTH_VIEW = "view"
_AUTH_MUTATE = "mutate"
_AUTH_APPROVE = "approve"
_AUTH_LIFECYCLE = "lifecycle"


@dataclass(frozen=True)
class CapabilityDecision:
    """One resolved capability: allowed, or denied with a stable reason."""

    allowed: bool
    reason: DisabledReason | None = None

    def __post_init__(self) -> None:
        if self.allowed and self.reason is not None:
            raise ValueError("an allowed capability must not carry a disabled reason")
        if not self.allowed and self.reason is None:
            raise ValueError("a denied capability must carry a stable disabled reason")


@dataclass(frozen=True)
class CallerAuthority:
    """The caller's MoonMind permission and approval authority for the session.

    Transcript visibility never implies approval, PTY input, file mutation,
    browser control, or cleanup authority (issue #3636 AC6): each is a separate
    flag. Use the classmethods for the common roles.
    """

    can_view: bool = False
    can_mutate: bool = False
    can_approve: bool = False
    can_manage_lifecycle: bool = False

    @classmethod
    def owner(cls) -> "CallerAuthority":
        return cls(can_view=True, can_mutate=True, can_approve=True, can_manage_lifecycle=True)

    @classmethod
    def administrator(cls) -> "CallerAuthority":
        return cls(can_view=True, can_mutate=True, can_approve=True, can_manage_lifecycle=True)

    @classmethod
    def read_only_viewer(cls) -> "CallerAuthority":
        return cls(can_view=True)

    @classmethod
    def approver(cls) -> "CallerAuthority":
        """A viewer with approval authority but no mutate/lifecycle authority."""
        return cls(can_view=True, can_approve=True)

    @classmethod
    def unauthorized(cls) -> "CallerAuthority":
        return cls()

    def has(self, authority: str) -> bool:
        return {
            _AUTH_VIEW: self.can_view,
            _AUTH_MUTATE: self.can_mutate,
            _AUTH_APPROVE: self.can_approve,
            _AUTH_LIFECYCLE: self.can_manage_lifecycle,
        }[authority]


@dataclass(frozen=True)
class SessionRuntimeState:
    """Current bridge/session/turn state that gates transient capabilities."""

    provider_bound: bool = False
    terminal: bool = False
    starting: bool = False
    active_turn_id: str | None = None
    elicitation_pending: bool = False

    @property
    def has_active_turn(self) -> bool:
        return bool(self.active_turn_id)


@dataclass(frozen=True)
class ImmutableExecutionAuthority:
    """Execution-bound immutable authority for one bound session.

    Built from the exact persisted effective launch snapshot (its content
    digest), the Provider Profile generation, and — when recorded — the Agent
    Profile digest/version. Absence of the launch snapshot / policy digest means
    there is no proven authority and the resolver fails closed.
    """

    policy_digest: str
    launch_snapshot_ref: str
    launch_policy_ref: str | None
    provider_profile_id: str | None
    provider_profile_generation: int | None
    agent_profile_digest: str | None
    agent_profile_version: int | None
    control_capabilities: frozenset[str]
    workspace_mutation_allowed: bool
    read_resources_allowed: bool
    model_change_allowed: bool
    effort_change_allowed: bool
    goal_change_allowed: bool

    @classmethod
    def from_evidence(
        cls,
        *,
        launch_snapshot: Mapping[str, Any] | None,
        provider_profile_id: str | None = None,
        provider_profile_generation: int | None = None,
        agent_profile: Mapping[str, Any] | None = None,
    ) -> "ImmutableExecutionAuthority | None":
        """Return proven authority, or ``None`` when it is missing.

        Returns ``None`` (fail closed) unless the launch snapshot carries a
        content ``snapshotRef`` and a validated ``policyAuthority`` digest.
        """

        if not isinstance(launch_snapshot, Mapping):
            return None
        snapshot_ref = str(launch_snapshot.get("snapshotRef") or "").strip()
        authority = launch_snapshot.get("policyAuthority")
        if not snapshot_ref or not isinstance(authority, Mapping):
            return None
        policy_digest = str(authority.get("policyDigest") or "").strip()
        if not policy_digest:
            return None

        control_caps = {
            str(cap).strip()
            for cap in (launch_snapshot.get("controlCapabilities") or [])
            if str(cap).strip()
        }
        session_boundary = {}
        boundaries = launch_snapshot.get("boundaries")
        if isinstance(boundaries, Mapping) and isinstance(
            boundaries.get("session"), Mapping
        ):
            session_boundary = boundaries["session"]

        agent_digest = None
        agent_version = None
        if isinstance(agent_profile, Mapping):
            agent_digest = str(agent_profile.get("digest") or "").strip() or None
            raw_version = agent_profile.get("version")
            if isinstance(raw_version, int):
                agent_version = raw_version

        return cls(
            policy_digest=policy_digest,
            launch_snapshot_ref=snapshot_ref,
            launch_policy_ref=str(launch_snapshot.get("launchPolicyRef") or "").strip()
            or None,
            provider_profile_id=(
                str(provider_profile_id).strip() or None
                if provider_profile_id is not None
                else str(launch_snapshot.get("providerProfileId") or "").strip() or None
            ),
            provider_profile_generation=provider_profile_generation,
            agent_profile_digest=agent_digest,
            agent_profile_version=agent_version,
            control_capabilities=frozenset(control_caps),
            workspace_mutation_allowed=bool(launch_snapshot.get("repositoryMutation")),
            read_resources_allowed=_snapshot_read_resources_allowed(launch_snapshot),
            # Per-session model/effort/goal change is pinned off by default: an
            # immutable profile-bound execution fixes them unless the launch
            # policy explicitly opts a session into changing them.
            model_change_allowed=bool(session_boundary.get("allowModelChange")),
            effort_change_allowed=bool(session_boundary.get("allowEffortChange")),
            goal_change_allowed=bool(session_boundary.get("allowGoalChange")),
        )

    def is_stale(
        self,
        *,
        expected_policy_digest: str | None = None,
        expected_launch_snapshot_ref: str | None = None,
        expected_agent_profile_digest: str | None = None,
        expected_provider_profile_generation: int | None = None,
    ) -> bool:
        """Return whether a caller's expected authority no longer matches.

        Any provided expectation that disagrees with this bound authority marks
        the request stale so it fails closed (issue #3636 AC9). Unset
        expectations are not compared.
        """

        if expected_policy_digest and expected_policy_digest != self.policy_digest:
            return True
        if (
            expected_launch_snapshot_ref
            and expected_launch_snapshot_ref != self.launch_snapshot_ref
        ):
            return True
        if (
            expected_agent_profile_digest
            and self.agent_profile_digest
            and expected_agent_profile_digest != self.agent_profile_digest
        ):
            return True
        if (
            expected_provider_profile_generation is not None
            and self.provider_profile_generation is not None
            and expected_provider_profile_generation
            != self.provider_profile_generation
        ):
            return True
        return False


def _snapshot_read_resources_allowed(launch_snapshot: Mapping[str, Any]) -> bool:
    follow_up = launch_snapshot.get("followUpRetrieval")
    if isinstance(follow_up, Mapping):
        return bool(follow_up.get("enabled"))
    return False


@dataclass(frozen=True)
class UpstreamCapabilityEvidence:
    """Verified upstream (host/provider) capability advertisement.

    Upstream support is a *technical* signal only; it never grants MoonMind
    permission. A capability the upstream did not advertise fails closed with
    ``unsupported_upstream`` so the native UI can distinguish that from a
    MoonMind policy or permission denial.
    """

    _flags: Mapping[str, bool]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "UpstreamCapabilityEvidence":
        flags: dict[str, bool] = {}
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                if isinstance(key, str) and isinstance(value, bool):
                    flags[key] = value
        return cls(flags)

    def supports(self, *keys: str) -> bool:
        return any(self._flags.get(key) is True for key in keys)


class CapabilityDenied(RuntimeError):
    """Raised by :meth:`EffectiveCapabilities.require` for a denied capability."""

    def __init__(self, capability: NativeCapability, reason: DisabledReason) -> None:
        super().__init__(f"capability {capability.value} denied: {reason.value}")
        self.capability = capability
        self.reason = reason


@dataclass(frozen=True)
class EffectiveCapabilities:
    """Versioned resolved capability set: browser manifest + server decision."""

    version: int
    decisions: Mapping[NativeCapability, CapabilityDecision]
    read_only: bool
    fail_closed_reason: DisabledReason | None = None

    def decision(self, capability: NativeCapability) -> CapabilityDecision:
        return self.decisions[capability]

    def allows(self, capability: NativeCapability) -> bool:
        return self.decisions[capability].allowed

    def require(self, capability: NativeCapability) -> None:
        """Enforce a capability server-side, raising :class:`CapabilityDenied`.

        This is the single enforcement primitive. A mutation handler must call
        it on every request regardless of what the browser rendered, so a
        hidden or disabled control that is invoked directly still fails closed.
        """

        decision = self.decisions[capability]
        if not decision.allowed:
            assert decision.reason is not None  # invariant from CapabilityDecision
            raise CapabilityDenied(capability, decision.reason)

    def manifest(self) -> dict[str, bool]:
        """Return the browser-safe ``camelCase`` capability -> bool map."""

        return {
            _MANIFEST_KEYS[cap]: decision.allowed
            for cap, decision in self.decisions.items()
        }

    def disabled_reasons(self) -> dict[str, str]:
        """Return browser-safe stable reasons for every denied capability."""

        return {
            _MANIFEST_KEYS[cap]: decision.reason.value
            for cap, decision in self.decisions.items()
            if decision.reason is not None
        }


@dataclass(frozen=True)
class _CapabilityRule:
    """Declarative resolution rule for one capability.

    ``authority`` is the caller authority required (beyond ``can_view``).
    ``upstream_keys`` are the upstream evidence keys that prove technical
    support (empty for MoonMind-owned operations). ``kind`` decides how the rule
    behaves against a terminal/read-only session: ``read`` capabilities survive
    a terminal session; ``mutate`` and ``lifecycle`` do not.
    """

    authority: str
    kind: str  # "read" | "mutate" | "lifecycle"
    upstream_keys: tuple[str, ...] = ()
    needs_active_turn: bool = False
    needs_elicitation: bool = False
    policy_control: str | None = None
    needs_workspace_mutation: bool = False
    needs_read_resources: bool = False
    pin_flag: str | None = None  # attr on ImmutableExecutionAuthority


# The single capability rule table. Order matches NativeCapability for a stable
# manifest projection.
_RULES: dict[NativeCapability, _CapabilityRule] = {
    NativeCapability.VIEW_TRANSCRIPT: _CapabilityRule(_AUTH_VIEW, "read"),
    NativeCapability.SEND_MESSAGE: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("sendMessage", "sendFollowUp")
    ),
    NativeCapability.QUEUE_MESSAGE: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("queueMessage", "steerMessage")
    ),
    NativeCapability.INTERRUPT_TURN: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("interruptTurn",),
        needs_active_turn=True,
        policy_control=_POLICY_INTERRUPT,
    ),
    NativeCapability.RESOLVE_ELICITATION: _CapabilityRule(
        _AUTH_APPROVE,
        "mutate",
        upstream_keys=("resolveElicitation",),
        needs_elicitation=True,
    ),
    NativeCapability.STOP_SESSION: _CapabilityRule(
        _AUTH_LIFECYCLE,
        "mutate",
        upstream_keys=("stopSession", "stop"),
        policy_control=_POLICY_TERMINATE,
    ),
    NativeCapability.CLEAR_SESSION: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("clearSession",),
        policy_control=_POLICY_CLEAR,
    ),
    NativeCapability.READ_RESOURCES: _CapabilityRule(
        _AUTH_VIEW, "read", upstream_keys=("readResources", "readLiveResources"),
        needs_read_resources=True,
    ),
    NativeCapability.UPLOAD_FILES: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("uploadFiles",)
    ),
    NativeCapability.MUTATE_WORKSPACE: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("mutateWorkspace",),
        needs_workspace_mutation=True,
    ),
    NativeCapability.CREATE_TERMINAL: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("createTerminal",),
        needs_workspace_mutation=True,
    ),
    NativeCapability.WRITE_TERMINAL: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("writeTerminal",),
        needs_workspace_mutation=True,
    ),
    NativeCapability.VIEW_TERMINAL: _CapabilityRule(
        _AUTH_VIEW, "read", upstream_keys=("viewTerminal",)
    ),
    NativeCapability.CLOSE_TERMINAL: _CapabilityRule(
        _AUTH_MUTATE,
        "mutate",
        upstream_keys=("closeTerminal",),
        needs_workspace_mutation=True,
    ),
    NativeCapability.OPEN_BROWSER: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("openBrowser",)
    ),
    NativeCapability.VIEW_SUB_AGENTS: _CapabilityRule(
        _AUTH_VIEW, "read", upstream_keys=("viewSubAgents",)
    ),
    NativeCapability.CONTROL_SUB_AGENTS: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("controlSubAgents",)
    ),
    NativeCapability.CHANGE_MODEL: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("changeModel",), pin_flag="model_change_allowed"
    ),
    NativeCapability.CHANGE_EFFORT: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("changeEffort",), pin_flag="effort_change_allowed"
    ),
    NativeCapability.CHANGE_GOAL: _CapabilityRule(
        _AUTH_MUTATE, "mutate", upstream_keys=("changeGoal",), pin_flag="goal_change_allowed"
    ),
    NativeCapability.RECONNECT_SESSION: _CapabilityRule(_AUTH_MUTATE, "mutate"),
    NativeCapability.HARVEST_EVIDENCE: _CapabilityRule(_AUTH_LIFECYCLE, "read"),
    NativeCapability.CLEANUP_SESSION: _CapabilityRule(_AUTH_LIFECYCLE, "lifecycle"),
}

_AUTHORITY_REASONS = {
    _AUTH_MUTATE: DisabledReason.REQUIRES_MUTATE_AUTHORITY,
    _AUTH_APPROVE: DisabledReason.REQUIRES_APPROVAL_AUTHORITY,
    _AUTH_LIFECYCLE: DisabledReason.REQUIRES_LIFECYCLE_AUTHORITY,
    _AUTH_VIEW: DisabledReason.REQUIRES_MUTATE_AUTHORITY,
}


def _evaluate_rule(
    capability: NativeCapability,
    rule: _CapabilityRule,
    *,
    immutable: ImmutableExecutionAuthority,
    session: SessionRuntimeState,
    caller: CallerAuthority,
    upstream: UpstreamCapabilityEvidence,
) -> CapabilityDecision:
    """Apply the capability intersection with a stable precedence of reasons.

    Precedence (most fundamental first) keeps the reason the native UI shows
    deterministic: caller permission → session lifecycle → immutable policy /
    pin → upstream support → transient turn/elicitation state.
    """

    # 1. Caller permission (transcript visibility never implies more).
    if not caller.can_view:
        return CapabilityDecision(False, DisabledReason.REQUIRES_MUTATE_AUTHORITY)
    if not caller.has(rule.authority):
        return CapabilityDecision(False, _AUTHORITY_REASONS[rule.authority])

    # 2. Session lifecycle. Reads survive a terminal (read-only) session so the
    #    transcript and resources remain inspectable; mutations do not.
    if not session.provider_bound:
        return CapabilityDecision(False, DisabledReason.SESSION_NOT_BOUND)
    if rule.kind != "read":
        if session.terminal:
            return CapabilityDecision(False, DisabledReason.SESSION_TERMINAL)
        if session.starting:
            return CapabilityDecision(False, DisabledReason.SESSION_STARTING)

    # 3. Immutable policy / profile pin.
    if rule.policy_control and rule.policy_control not in immutable.control_capabilities:
        return CapabilityDecision(False, DisabledReason.POLICY_FORBIDS)
    if rule.needs_workspace_mutation and not immutable.workspace_mutation_allowed:
        return CapabilityDecision(False, DisabledReason.POLICY_FORBIDS)
    if rule.needs_read_resources and not immutable.read_resources_allowed:
        return CapabilityDecision(False, DisabledReason.POLICY_FORBIDS)
    if rule.pin_flag is not None and not getattr(immutable, rule.pin_flag):
        return CapabilityDecision(False, DisabledReason.PINNED_BY_PROFILE)

    # 4. Upstream technical support (MoonMind-owned ops declare no keys).
    if rule.upstream_keys and not upstream.supports(*rule.upstream_keys):
        return CapabilityDecision(False, DisabledReason.UNSUPPORTED_UPSTREAM)

    # 5. Transient turn / elicitation state.
    if rule.needs_active_turn and not session.has_active_turn:
        return CapabilityDecision(False, DisabledReason.NO_ACTIVE_TURN)
    if rule.needs_elicitation and not session.elicitation_pending:
        return CapabilityDecision(False, DisabledReason.NO_ACTIVE_ELICITATION)

    return CapabilityDecision(True)


def fail_closed(reason: DisabledReason) -> EffectiveCapabilities:
    """Return an all-denied capability set carrying one bounded reason."""

    return EffectiveCapabilities(
        version=CAPABILITY_SCHEMA_VERSION,
        decisions={
            cap: CapabilityDecision(False, reason) for cap in NativeCapability
        },
        read_only=True,
        fail_closed_reason=reason,
    )


def resolve_effective_capabilities(
    *,
    immutable: ImmutableExecutionAuthority | None,
    session: SessionRuntimeState,
    caller: CallerAuthority,
    upstream: UpstreamCapabilityEvidence,
    expected_policy_digest: str | None = None,
    expected_launch_snapshot_ref: str | None = None,
    expected_agent_profile_digest: str | None = None,
    expected_provider_profile_generation: int | None = None,
) -> EffectiveCapabilities:
    """Resolve the one effective capability set for a bound native session.

    Fails closed (all denied) when immutable authority is missing, or when a
    caller's expected authority no longer matches the bound authority (stale
    session/profile/generation). Otherwise returns per-capability decisions with
    stable reasons, usable both as the browser manifest and the server decision.
    """

    if immutable is None:
        return fail_closed(DisabledReason.MISSING_AUTHORITY)
    if immutable.is_stale(
        expected_policy_digest=expected_policy_digest,
        expected_launch_snapshot_ref=expected_launch_snapshot_ref,
        expected_agent_profile_digest=expected_agent_profile_digest,
        expected_provider_profile_generation=expected_provider_profile_generation,
    ):
        return fail_closed(DisabledReason.STALE_AUTHORITY)

    decisions = {
        capability: _evaluate_rule(
            capability,
            rule,
            immutable=immutable,
            session=session,
            caller=caller,
            upstream=upstream,
        )
        for capability, rule in _RULES.items()
    }
    read_only = not decisions[NativeCapability.SEND_MESSAGE].allowed
    return EffectiveCapabilities(
        version=CAPABILITY_SCHEMA_VERSION,
        decisions=decisions,
        read_only=read_only,
    )


__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "NativeCapability",
    "DisabledReason",
    "CapabilityDecision",
    "CapabilityDenied",
    "CallerAuthority",
    "SessionRuntimeState",
    "ImmutableExecutionAuthority",
    "UpstreamCapabilityEvidence",
    "EffectiveCapabilities",
    "resolve_effective_capabilities",
    "fail_closed",
]
