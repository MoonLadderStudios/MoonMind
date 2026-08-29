"""Bind every canonical turn to the immutable execution plan it may reuse.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

Turn admission is the one boundary that decides whether a new instruction may
reuse an existing canonical session. It loads the recorded execution plan and
runtime binding (#3706) from durable session authority, compares them against
the authority the caller is *requesting*, and returns one typed
:class:`~moonmind.omnigent.resume_decision.SessionResumeOutcome`.

Changed immutable authority never silently mutates the prior session: it returns
``branch_required`` (or ``new_session_required`` when the prior session cannot be
branched from) before any provider mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from moonmind.omnigent.resume_decision import (
    SessionResumeDecision,
    SessionResumeOutcome,
)

from .records import compute_digest


#: Key under which a canonical session records the immutable authority its turns
#: must preserve. Stored on session metadata so it survives host, credential, and
#: workspace cleanup alongside the session's historical-read authority.
IMMUTABLE_AUTHORITY_METADATA_KEY = "immutableTurnAuthority"

#: The closed set of immutable execution dimensions a same-session turn may not
#: change. Every dimension named by MoonLadderStudios/MoonMind#3707 §2 is here:
#: harness and execution realizer, model, repository, workspace, Skill, launch,
#: and publication authority, plus Provider Profile generation.
IMMUTABLE_TURN_AUTHORITY_DIMENSIONS: tuple[str, ...] = (
    "harnessId",
    "executionRealizerRef",
    "model",
    "effort",
    "providerProfileId",
    "providerProfileGeneration",
    "repository",
    "repositoryBranch",
    "workspaceIntentRef",
    "resolvedSkillsRef",
    "launchPolicyRef",
    "publishMode",
)


def _normalize(value: Any) -> Any:
    """Return a comparable, JSON-stable projection of one dimension value."""

    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        return compute_digest({str(k): _normalize(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return compute_digest([_normalize(item) for item in value])
    return str(value)


@dataclass(frozen=True)
class ImmutableTurnAuthority:
    """The execution authority one canonical session's turns must preserve."""

    execution_plan_ref: str | None = None
    runtime_binding_ref: str | None = None
    dimensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dimensions",
            {
                name: _normalize(self.dimensions.get(name))
                for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS
            },
        )

    @property
    def complete(self) -> bool:
        """True when every immutable dimension carries a concrete value."""

        return all(
            self.dimensions.get(name) is not None
            for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS
        )

    def missing_dimensions(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS
            if self.dimensions.get(name) is None
        )

    def as_metadata(self) -> dict[str, Any]:
        """Return the compact, non-sensitive durable projection."""

        return {
            "executionPlanRef": self.execution_plan_ref,
            "runtimeBindingRef": self.runtime_binding_ref,
            "dimensions": dict(self.dimensions),
        }

    @classmethod
    def from_metadata(
        cls, payload: Mapping[str, Any] | None
    ) -> "ImmutableTurnAuthority | None":
        if not isinstance(payload, Mapping):
            return None
        dimensions = payload.get("dimensions")
        return cls(
            execution_plan_ref=payload.get("executionPlanRef") or None,
            runtime_binding_ref=payload.get("runtimeBindingRef") or None,
            dimensions=dimensions if isinstance(dimensions, Mapping) else {},
        )

    @classmethod
    def from_execution_plan(
        cls,
        plan: Any,
        *,
        runtime_binding_ref: str | None = None,
        repository: str | None = None,
        repository_branch: str | None = None,
        publish_mode: str | None = None,
        provider_profile_id: str | None = None,
        provider_profile_generation: int | None = None,
    ) -> "ImmutableTurnAuthority":
        """Project an admitted execution plan envelope into turn authority.

        The projection is harness-neutral: it reads the recorded plan payload,
        never a Codex- or OpenCode-specific lifecycle branch.
        """

        payload = getattr(plan, "payload", None)
        model_config = getattr(payload, "modelConfig", None)
        return cls(
            execution_plan_ref=str(getattr(plan, "planRef", "") or "") or None,
            runtime_binding_ref=runtime_binding_ref,
            dimensions={
                "harnessId": getattr(payload, "harnessId", None),
                "executionRealizerRef": getattr(
                    payload, "executionRealizerRef", None
                ),
                "model": getattr(model_config, "model", None),
                "effort": getattr(model_config, "effort", None),
                "providerProfileId": provider_profile_id,
                "providerProfileGeneration": provider_profile_generation,
                "repository": repository,
                "repositoryBranch": repository_branch,
                "workspaceIntentRef": getattr(payload, "workspaceIntentRef", None),
                "resolvedSkillsRef": getattr(payload, "resolvedSkills", None),
                "launchPolicyRef": getattr(payload, "launchPolicyRef", None),
                "publishMode": publish_mode,
            },
        )


#: Dimensions a remediation attempt may never change. MoonLadderStudios/MoonMind
#: #3707 AC6: remediation is bounded by the authority of the attempt it repairs,
#: so harness, execution realizer, Provider Profile, model, workspace, Skill,
#: launch policy, and publication authority are all off-limits. Changing any of
#: them is a policy violation, not a routine branch decision.
REMEDIATION_NON_BROADENING_DIMENSIONS: tuple[str, ...] = (
    "harnessId",
    "executionRealizerRef",
    "providerProfileId",
    "model",
    "workspaceIntentRef",
    "resolvedSkillsRef",
    "launchPolicyRef",
    "publishMode",
)


class RemediationAuthorityBroadenedError(RuntimeError):
    """A remediation attempt tried to widen the authority it may exercise."""

    def __init__(self, broadened: tuple[str, ...]) -> None:
        self.broadened = broadened
        super().__init__(
            "remediation may not broaden execution authority: "
            + ",".join(broadened)
        )


def assert_remediation_does_not_broaden(
    *,
    recorded: ImmutableTurnAuthority | None,
    requested: ImmutableTurnAuthority | None,
) -> None:
    """Fail closed when a remediation turn changes bounded execution authority.

    Dimensions the remediation does not assert are left to the session's
    recorded authority; only an asserted *different* value is a broadening.
    """

    if recorded is None or requested is None:
        return
    broadened = tuple(
        name
        for name in REMEDIATION_NON_BROADENING_DIMENSIONS
        if requested.dimensions.get(name) is not None
        and recorded.dimensions.get(name) is not None
        and requested.dimensions.get(name) != recorded.dimensions.get(name)
    )
    if broadened:
        raise RemediationAuthorityBroadenedError(broadened)


class CanonicalTurnAdmissionRejected(RuntimeError):
    """A turn was refused before any provider mutation.

    Carries the typed :class:`SessionResumeOutcome` so the caller branches on the
    closed decision vocabulary instead of parsing a message.
    """

    def __init__(self, outcome: SessionResumeOutcome) -> None:
        self.outcome = outcome
        super().__init__(
            f"canonical turn admission returned {outcome.decision.value}: "
            + ",".join(outcome.reason_codes)
        )

    @property
    def decision(self) -> SessionResumeDecision:
        return self.outcome.decision


def evaluate_turn_admission(
    *,
    recorded: ImmutableTurnAuthority | None,
    requested: ImmutableTurnAuthority | None,
    session_terminal: bool = False,
    session_resumable: bool = True,
    runtime_authority_current: bool = True,
    branch_capable: bool = True,
    cleanup_complete: bool = False,
    require_complete_authority: bool = True,
) -> SessionResumeOutcome:
    """Return the one typed decision for reusing this canonical session.

    Ordering is deliberate and evidence-gated:

    1. A durably terminal, non-resumable session can never be mutated.
    2. Missing authority evidence fails closed when the caller asserts a
       complete immutable set (checkpoint resume). A follow-up instruction that
       simply does not restate a dimension is *not* asserting a change, so with
       ``require_complete_authority=False`` only jointly-present dimensions are
       compared.
    3. Changed immutable authority branches (or requires a new session when no
       branch-capable evidence exists) -- it never rewrites the prior session.
    4. Only then does live-versus-cold runtime availability matter; completed
       cleanup means live runtime authority is gone by definition.
    """

    if session_terminal and not session_resumable:
        return SessionResumeOutcome(
            decision=(
                SessionResumeDecision.BRANCH_REQUIRED
                if branch_capable
                else SessionResumeDecision.NEW_SESSION_REQUIRED
            ),
            reason_codes=("session_terminal",),
        )
    if requested is None:
        return SessionResumeOutcome(
            decision=SessionResumeDecision.RESUME_UNAVAILABLE,
            reason_codes=("requested_authority_missing",),
        )
    if recorded is None:
        return SessionResumeOutcome(
            decision=SessionResumeDecision.RESUME_UNAVAILABLE,
            reason_codes=("recorded_authority_missing",),
        )
    if require_complete_authority:
        missing = tuple(
            f"immutable_{name}_missing"
            for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS
            if recorded.dimensions.get(name) is None
            or requested.dimensions.get(name) is None
        )
        if missing:
            return SessionResumeOutcome(
                decision=SessionResumeDecision.RESUME_UNAVAILABLE,
                reason_codes=missing,
            )
    changed: list[str] = []
    if (
        recorded.execution_plan_ref
        and requested.execution_plan_ref
        and recorded.execution_plan_ref != requested.execution_plan_ref
    ):
        changed.append("immutable_executionPlanRef_changed")
    for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS:
        recorded_value = recorded.dimensions.get(name)
        requested_value = requested.dimensions.get(name)
        if recorded_value is None or requested_value is None:
            # Not asserted by this instruction; the session keeps its authority.
            continue
        if recorded_value != requested_value:
            changed.append(f"immutable_{name}_changed")
    if changed:
        return SessionResumeOutcome(
            decision=(
                SessionResumeDecision.BRANCH_REQUIRED
                if branch_capable
                else SessionResumeDecision.NEW_SESSION_REQUIRED
            ),
            reason_codes=tuple(changed),
        )
    if runtime_authority_current and not cleanup_complete:
        return SessionResumeOutcome(
            decision=SessionResumeDecision.LIVE_REATTACH,
            reason_codes=("all_authority_valid",),
        )
    return SessionResumeOutcome(
        decision=SessionResumeDecision.COLD_RESTORE,
        reason_codes=(
            ("cleanup_complete",) if cleanup_complete else ("live_authority_unavailable",)
        ),
    )


__all__ = [
    "CanonicalTurnAdmissionRejected",
    "IMMUTABLE_AUTHORITY_METADATA_KEY",
    "IMMUTABLE_TURN_AUTHORITY_DIMENSIONS",
    "ImmutableTurnAuthority",
    "REMEDIATION_NON_BROADENING_DIMENSIONS",
    "RemediationAuthorityBroadenedError",
    "assert_remediation_does_not_broaden",
    "evaluate_turn_admission",
]
