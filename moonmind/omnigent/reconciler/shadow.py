"""Shadow-mode comparison for the Omnigent lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

This lets the reconciler run *alongside* the existing execution path so its
decision can be compared to whatever action the legacy code chose, **without**
making the reconciler a second orchestration source of truth: nothing here
executes, mutates, or persists. It only produces a bounded, credential-free
comparison record that a caller may log or store.

The mapping from legacy action labels to the closed decision vocabulary is
explicit (no display-name matching or provider synonyms). Unknown legacy labels
surface as a divergence, never a silent success. No I/O is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass

from moonmind.omnigent.reconciler.decision import ReconciliationDecision
from moonmind.omnigent.reconciler.vocabulary import DecisionAction

# Explicit legacy-action -> canonical decision mapping. Keys are the legacy
# execution-path labels; values are the closed vocabulary this reducer emits.
LEGACY_ACTION_ALIASES: dict[str, DecisionAction] = {
    "noop": DecisionAction.NO_OP,
    "no_op": DecisionAction.NO_OP,
    "wait": DecisionAction.AWAIT_OBSERVATION,
    "poll": DecisionAction.AWAIT_OBSERVATION,
    "await": DecisionAction.AWAIT_OBSERVATION,
    "acquire_profile_lease": DecisionAction.ENSURE_PROFILE_LEASE,
    "ensure_profile_lease": DecisionAction.ENSURE_PROFILE_LEASE,
    "register_host": DecisionAction.ENSURE_HOST,
    "ensure_host": DecisionAction.ENSURE_HOST,
    "create_session": DecisionAction.ENSURE_PROVIDER_SESSION,
    "ensure_provider_session": DecisionAction.ENSURE_PROVIDER_SESSION,
    "send_turn": DecisionAction.SUBMIT_TURN,
    "submit_turn": DecisionAction.SUBMIT_TURN,
    "record_terminal": DecisionAction.RECORD_PROVIDER_TERMINAL,
    "record_provider_terminal": DecisionAction.RECORD_PROVIDER_TERMINAL,
    "synthesize_terminal": DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    "snapshot_reconcile": DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    "harvest": DecisionAction.HARVEST_EVIDENCE,
    "harvest_evidence": DecisionAction.HARVEST_EVIDENCE,
    "cleanup": DecisionAction.BEGIN_CLEANUP,
    "begin_cleanup": DecisionAction.BEGIN_CLEANUP,
    "release_leases": DecisionAction.RELEASE_LEASES,
    "release": DecisionAction.RELEASE_LEASES,
    "retry": DecisionAction.RETRY_TRANSIENT_OBSERVATION,
    "quarantine": DecisionAction.QUARANTINE_AMBIGUOUS_STATE,
    "fail": DecisionAction.FAIL_NONRETRYABLE,
    "fail_nonretryable": DecisionAction.FAIL_NONRETRYABLE,
}

_MAX_NOTE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    """A bounded, credential-free comparison of legacy vs. reconciler action."""

    session_id: str
    legacy_action: str
    reconciler_action: str
    reason_codes: tuple[str, ...]
    agree: bool
    legacy_recognized: bool
    note: str = ""

    def to_log_dict(self) -> dict[str, object]:
        """Return a bounded, non-secret projection safe for diagnostics.

        Contains only decision vocabulary, reason codes, the canonical session
        id, and a truncated note. It never carries provider-session, host,
        profile, lease, credential, workspace, or user identity.
        """

        return {
            "schema": "moonmind.omnigent.reconciler.shadow.v1",
            "sessionId": self.session_id,
            "legacyAction": self.legacy_action,
            "reconcilerAction": self.reconciler_action,
            "reasonCodes": list(self.reason_codes),
            "agree": self.agree,
            "legacyRecognized": self.legacy_recognized,
            "note": self.note[:_MAX_NOTE_LENGTH],
        }


def compare_shadow(
    *,
    session_id: str,
    legacy_action: str,
    decision: ReconciliationDecision,
) -> ShadowComparison:
    """Compare a legacy action label to a reconciler decision.

    Pure and side-effect free. An unrecognized legacy label yields
    ``agree=False`` with ``legacy_recognized=False`` so unexplained divergence is
    visible to characterization tests rather than silently treated as agreement.
    """

    normalized = str(legacy_action or "").strip().lower()
    mapped = LEGACY_ACTION_ALIASES.get(normalized)
    reconciler_action = decision.action.value
    if mapped is None:
        return ShadowComparison(
            session_id=session_id,
            legacy_action=normalized,
            reconciler_action=reconciler_action,
            reason_codes=decision.reason_code_values,
            agree=False,
            legacy_recognized=False,
            note=f"unrecognized legacy action {normalized!r}",
        )
    agree = mapped is decision.action
    note = "" if agree else f"legacy {mapped.value} != reconciler {reconciler_action}"
    return ShadowComparison(
        session_id=session_id,
        legacy_action=normalized,
        reconciler_action=reconciler_action,
        reason_codes=decision.reason_code_values,
        agree=agree,
        legacy_recognized=True,
        note=note,
    )


__all__ = [
    "LEGACY_ACTION_ALIASES",
    "ShadowComparison",
    "compare_shadow",
]
