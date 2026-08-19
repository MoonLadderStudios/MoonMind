"""Independently-supported rollback controls for the Omnigent session supervisor.

Source issue: MoonLadderStudios/MoonMind#3712.

Rollback for the ``MoonMind.OmnigentSession`` supervisor is expressed as a set of
independent controls, not one global kill switch. Each control affects only new
work; already-admitted sessions keep running under their recorded execution
owner. Two rules are absolute and hold in every mode:

* Rollback never silently substitutes direct Codex (or any other profile, host
  mode, policy, or session) for an Omnigent request.
* Rollback never mutates the immutable profile, policy, model, workspace, or
  image authority of an active session, never transfers live provider
  side-effect ownership without a fenced handoff, never erases canonical or
  legacy evidence, never invalidates historical chat-binding URLs, and never
  releases Provider Profile capacity before credential consumers stop.

The five independently-supported controls (issue #3712 "Rollback behavior") map
onto :class:`RollbackMode`:

``none``
    Normal operation; no rollback restriction.
``disable_new_admission``
    Block new supervisor admission; existing new sessions continue.
``disable_new_selection``
    Block new Omnigent selection; cleanup, replay, and existing sessions
    continue.
``chat_read_only``
    Disable interactive native chat while preserving diagnostic and historical
    reads.
``revert_default_to_legacy``
    Revert the default for new sessions to the legacy path, only while the
    legacy path is explicitly supported.
``complete_stop``
    Stop all new Omnigent work without substituting direct Codex.
"""

from __future__ import annotations

from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

SUPERVISOR_ROLLBACK_POLICY_VERSION = "moonmind.omnigent-session-supervisor-rollback/v1"

RollbackMode = Literal[
    "none",
    "disable_new_admission",
    "disable_new_selection",
    "chat_read_only",
    "revert_default_to_legacy",
    "complete_stop",
]

_ROLLBACK_MODES: frozenset[str] = frozenset(
    (
        "none",
        "disable_new_admission",
        "disable_new_selection",
        "chat_read_only",
        "revert_default_to_legacy",
        "complete_stop",
    )
)


class SessionRollbackContext(BaseModel):
    """State of the session (or fleet) a rollback effect is being resolved for."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    is_active: bool = Field(False, alias="isActive")
    admitted_via_supervisor: bool = Field(False, alias="admittedViaSupervisor")
    recorded_execution_owner: str = Field("", alias="recordedExecutionOwner")
    legacy_path_supported: bool = Field(True, alias="legacyPathSupported")
    credential_consumers_stopped: bool = Field(
        False, alias="credentialConsumersStopped"
    )
    cleanup_complete: bool = Field(False, alias="cleanupComplete")


class RollbackEffect(BaseModel):
    """Resolved, fail-closed effect of a rollback control.

    The four leading booleans are the independent controls. The remaining fields
    are preserved-capability guarantees and absolute safety invariants that hold
    in every mode.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    mode: RollbackMode
    reason_code: str = Field(alias="reasonCode")

    # Independent new-work controls.
    new_supervisor_admission_allowed: bool = Field(
        alias="newSupervisorAdmissionAllowed"
    )
    new_omnigent_selection_allowed: bool = Field(alias="newOmnigentSelectionAllowed")
    interactive_native_chat_allowed: bool = Field(
        alias="interactiveNativeChatAllowed"
    )
    legacy_default_for_new_sessions: bool = Field(
        alias="legacyDefaultForNewSessions"
    )

    # Preserved capabilities for already-admitted / historical sessions.
    existing_session_continues_under_recorded_owner: bool = Field(
        alias="existingSessionContinuesUnderRecordedOwner"
    )
    cleanup_preserved: bool = Field(alias="cleanupPreserved")
    replay_preserved: bool = Field(alias="replayPreserved")
    historical_reads_preserved: bool = Field(alias="historicalReadsPreserved")
    diagnostic_reads_preserved: bool = Field(alias="diagnosticReadsPreserved")
    chat_binding_urls_preserved: bool = Field(alias="chatBindingUrlsPreserved")
    evidence_preserved: bool = Field(alias="evidencePreserved")

    # Absolute safety invariants.
    direct_codex_substitution: bool = Field(alias="directCodexSubstitution")
    mutates_active_session_authority: bool = Field(
        alias="mutatesActiveSessionAuthority"
    )
    fenced_handoff_required_for_ownership_transfer: bool = Field(
        alias="fencedHandoffRequiredForOwnershipTransfer"
    )
    provider_capacity_release_allowed: bool = Field(
        alias="providerCapacityReleaseAllowed"
    )

    policy_version: str = Field(
        SUPERVISOR_ROLLBACK_POLICY_VERSION, alias="policyVersion"
    )

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def parse_rollback_mode(value: object) -> RollbackMode:
    """Resolve a configured rollback mode; unsupported values fail closed.

    Fail-fast is preferred over hidden fallback (Compatibility Policy): an
    unknown mode raises rather than silently degrading to ``none``.
    """

    normalized = str(value or "none").strip().lower().replace("-", "_")
    if not normalized:
        return "none"
    if normalized not in _ROLLBACK_MODES:
        raise ValueError(f"unsupported Omnigent supervisor rollback mode: {value!r}")
    return normalized  # type: ignore[return-value]


def rollback_mode_from_settings(feature_flags: object) -> RollbackMode:
    """Read the operator-configured rollback mode from feature flags."""

    return parse_rollback_mode(
        getattr(feature_flags, "omnigent_session_supervisor_rollback_mode", "none")
    )


# Per-mode settings for the four independent new-work controls. ``None`` for the
# legacy-default control means it is decided from context (legacy support).
_MODE_CONTROLS: Mapping[str, tuple[bool, bool, bool, bool | None]] = {
    # mode: (admission, selection, chat, legacy_default)
    "none": (True, True, True, False),
    "disable_new_admission": (False, True, True, False),
    "disable_new_selection": (False, False, True, False),
    "chat_read_only": (True, True, False, False),
    "revert_default_to_legacy": (False, True, True, None),
    "complete_stop": (False, False, False, False),
}


def resolve_rollback_effect(
    *, mode: RollbackMode, context: SessionRollbackContext | None = None
) -> RollbackEffect:
    """Resolve the fail-closed effect of a rollback control.

    ``revert_default_to_legacy`` is only honored while the legacy path is
    explicitly supported; otherwise the effect fails closed (blocks new work)
    rather than silently substituting another execution path.
    """

    ctx = context or SessionRollbackContext()
    admission, selection, chat, legacy_default = _MODE_CONTROLS[mode]
    reason = mode

    if mode == "revert_default_to_legacy":
        if ctx.legacy_path_supported:
            legacy_default = True
            reason = "revert_default_to_legacy"
        else:
            # Cannot honor a legacy default when the legacy path is unsupported.
            # Fail closed on new work instead of rerouting to any other path.
            legacy_default = False
            selection = False
            reason = "legacy_path_unsupported"

    assert legacy_default is not None  # resolved above for every mode

    return RollbackEffect(
        mode=mode,
        reasonCode=reason,
        newSupervisorAdmissionAllowed=admission,
        newOmnigentSelectionAllowed=selection,
        interactiveNativeChatAllowed=chat,
        legacyDefaultForNewSessions=legacy_default,
        # Already-admitted sessions always keep running under their recorded
        # owner; new-work controls never touch them.
        existingSessionContinuesUnderRecordedOwner=True,
        cleanupPreserved=True,
        replayPreserved=True,
        historicalReadsPreserved=True,
        diagnosticReadsPreserved=True,
        chatBindingUrlsPreserved=True,
        evidencePreserved=True,
        # Absolute invariants.
        directCodexSubstitution=False,
        mutatesActiveSessionAuthority=False,
        fencedHandoffRequiredForOwnershipTransfer=True,
        # Releasing exclusive provider capacity requires the same terminal
        # evidence the production reconciliation policy demands
        # (``moonmind.omnigent.reconciler.reducer``): both durable cleanup
        # completion *and* confirmed consumer absence. Rollback must never free
        # a lease while cleanup authority still owns resources.
        providerCapacityReleaseAllowed=bool(
            ctx.credential_consumers_stopped and ctx.cleanup_complete
        ),
    )


__all__ = [
    "SUPERVISOR_ROLLBACK_POLICY_VERSION",
    "RollbackMode",
    "SessionRollbackContext",
    "RollbackEffect",
    "parse_rollback_mode",
    "rollback_mode_from_settings",
    "resolve_rollback_effect",
]
