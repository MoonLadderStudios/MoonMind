"""Independently-supported rollback controls for the Omnigent session supervisor.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835.

#3712 owns the five independent new-work controls below. #3835 adds the rollback
*scope* and *exercise record* the retirement guard consumes: a rollback is bound
to one exact combination of Agent Profile, Host Class, materializer, realizer,
model, launch policy, host mode, architecture, and owner cohort, and a legacy
path becomes removal-eligible only after a fresh, successful, exactly-scoped
exercise that changed future admission alone.

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

from datetime import datetime, timedelta
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPERVISOR_ROLLBACK_POLICY_VERSION = "moonmind.omnigent-session-supervisor-rollback/v1"
ROLLBACK_EXERCISE_POLICY_VERSION = "moonmind.omnigent-rollback-exercise/v1"

# A rollback exercise proves an operator can restore a supported prior default
# *now*, so an old recording cannot keep a path removal-eligible forever
# (issue #3835 required work section 6: rollback cannot silently activate a path
# whose support evidence has expired).
DEFAULT_ROLLBACK_EXERCISE_MAX_AGE = timedelta(days=30)

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
    # Terminal cleanup evidence. Releasing exclusive provider capacity requires
    # both confirmed consumer absence *and* completed cleanup, mirroring the
    # production reconciliation policy in
    # :mod:`moonmind.omnigent.reconciler.reducer`; a stopped consumer alone never
    # authorizes release while cleanup authority still owns resources.
    cleanup_completed: bool = Field(False, alias="cleanupCompleted")


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
        # Release exclusive provider capacity only when consumers have stopped
        # *and* cleanup is complete — never on a stopped consumer alone.
        providerCapacityReleaseAllowed=bool(
            ctx.credential_consumers_stopped and ctx.cleanup_completed
        ),
    )


class RollbackScope(BaseModel):
    """The exact combination one rollback exercise covers.

    Issue #3835 required work section 6 scopes rollback to the exact Agent
    Profile, Host Class, materializer, realizer, model, policy, host mode,
    architecture, and owner cohort. Every dimension is matched by exact equality
    so a rollback recorded for one combination can never quietly authorize a
    different, less-constrained one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    agent_profile_ref: str = Field(alias="agentProfileRef")
    host_class_ref: str = Field(alias="hostClassRef")
    materializer_ref: str = Field(alias="materializerRef")
    execution_realizer_ref: str = Field(alias="executionRealizerRef")
    model_qualified_id: str = Field(alias="modelQualifiedId")
    launch_policy_ref: str = Field(alias="launchPolicyRef")
    host_mode: str = Field(alias="hostMode")
    architecture: str
    owner_cohort: str = Field(alias="ownerCohort")

    @field_validator("*")
    @classmethod
    def _require_exact_value(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError(
                "every rollback scope dimension must name an exact value; a blank "
                "dimension would widen the rollback beyond what was exercised"
            )
        return cleaned


class RollbackExerciseRecord(BaseModel):
    """Durable evidence that rollback was actually performed for one scope."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    retirement_path_id: str = Field(alias="retirementPathId")
    scope: RollbackScope
    exercised_at: datetime = Field(alias="exercisedAt")
    evidence_ref: str = Field(alias="evidenceRef")
    succeeded: bool = Field(alias="succeeded")
    # Whether the exercise restored a prior default for *future* work only. A
    # recording that touched an active or historical execution is not usable
    # evidence: rollback must never rewrite work that already exists.
    future_admission_only: bool = Field(True, alias="futureAdmissionOnly")
    policy_version: str = Field(
        ROLLBACK_EXERCISE_POLICY_VERSION, alias="policyVersion"
    )

    @field_validator("exercised_at")
    @classmethod
    def _validate_exercised_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("exercised_at must be timezone-aware")
        return value


class RollbackExerciseDecision(BaseModel):
    """Whether a recorded rollback exercise satisfies a retirement row."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    retirement_path_id: str = Field(alias="retirementPathId")
    satisfied: bool
    reason_code: str = Field(alias="reasonCode")
    evidence_ref: str | None = Field(None, alias="evidenceRef")
    policy_version: str = Field(
        ROLLBACK_EXERCISE_POLICY_VERSION, alias="policyVersion"
    )

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def legacy_rollback_generation_from_settings(feature_flags: object) -> str | None:
    """The explicit rollback generation permitted to re-admit legacy new work.

    Legacy re-admission is not a separate switch: it is the existing supervisor
    generation, and only while the operator has explicitly selected the
    ``revert_default_to_legacy`` control. Any other mode yields ``None``, so a
    ``rollback_only`` retirement class stays closed to new work.
    """

    if rollback_mode_from_settings(feature_flags) != "revert_default_to_legacy":
        return None
    generation = str(
        getattr(feature_flags, "omnigent_session_supervisor_generation", "") or ""
    ).strip()
    return generation or None


def evaluate_rollback_exercise(
    *,
    retirement_path_id: str,
    scope: RollbackScope,
    records: Iterable[RollbackExerciseRecord],
    now: datetime,
    max_age: timedelta = DEFAULT_ROLLBACK_EXERCISE_MAX_AGE,
) -> RollbackExerciseDecision:
    """Whether a fresh, successful, exactly-scoped rollback exercise exists.

    Fail-closed on every axis: no record, a record for another path, any scope
    dimension that differs, a failed exercise, one that touched active or
    historical work, or expired evidence all leave the path *not* exercised and
    therefore not removal-eligible.
    """

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    reason = "no_rollback_exercise_recorded"
    for record in records:
        if record.retirement_path_id != retirement_path_id:
            continue
        if record.scope != scope:
            reason = "rollback_scope_mismatch"
            continue
        if not record.succeeded:
            reason = "rollback_exercise_failed"
            continue
        if not record.future_admission_only:
            reason = "rollback_exercise_touched_existing_work"
            continue
        if now - record.exercised_at > max_age:
            reason = "rollback_evidence_expired"
            continue
        return RollbackExerciseDecision(
            retirementPathId=retirement_path_id,
            satisfied=True,
            reasonCode="rollback_exercise_recorded",
            evidenceRef=record.evidence_ref,
        )
    return RollbackExerciseDecision(
        retirementPathId=retirement_path_id,
        satisfied=False,
        reasonCode=reason,
    )


__all__ = [
    "DEFAULT_ROLLBACK_EXERCISE_MAX_AGE",
    "ROLLBACK_EXERCISE_POLICY_VERSION",
    "SUPERVISOR_ROLLBACK_POLICY_VERSION",
    "RollbackMode",
    "RollbackExerciseDecision",
    "RollbackExerciseRecord",
    "RollbackScope",
    "SessionRollbackContext",
    "RollbackEffect",
    "evaluate_rollback_exercise",
    "legacy_rollback_generation_from_settings",
    "parse_rollback_mode",
    "rollback_mode_from_settings",
    "resolve_rollback_effect",
]
