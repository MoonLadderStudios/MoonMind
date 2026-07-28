"""Evidence-gated Codex-through-Omnigent rollout contract.

Source issue: MoonLadderStudios/MoonMind#3518.

The contract intentionally decides promotion only. Runtime selection remains an
explicit authored value; a denied promotion never causes a direct-Codex
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Mapping

CUTOVER_POLICY_VERSION = "moonmind.codex-omnigent-cutover/v1"
MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60


class CutoverPhase(IntEnum):
    OPT_IN = 1
    CREATE_DEFAULT = 2
    SCHEDULE_DEFAULT = 3
    BROAD_DEFAULT = 4
    DIRECT_LAUNCH_DISABLED = 5
    DIRECT_LAUNCH_REMOVED = 6


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    current_phase: CutoverPhase
    requested_phase: CutoverPhase
    blockers: tuple[str, ...]
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "allowed": self.allowed,
            "currentPhase": self.current_phase.name.lower(),
            "requestedPhase": self.requested_phase.name.lower(),
            "blockers": list(self.blockers),
        }


def evaluate_promotion(
    *,
    current_phase: CutoverPhase,
    requested_phase: CutoverPhase,
    evidence: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> PromotionDecision:
    """Fail closed unless the next phase has fresh, complete live evidence.

    Promotion is deliberately one phase at a time. Rollback to an earlier phase
    is always allowed because it does not rewrite persisted runtime identity.
    """

    if requested_phase <= current_phase:
        return PromotionDecision(True, current_phase, requested_phase, ())

    blockers: list[str] = []
    if requested_phase != current_phase + 1:
        blockers.append("promotion_must_advance_one_phase")
    if not evidence:
        blockers.append("live_conformance_evidence_missing")
        return PromotionDecision(False, current_phase, requested_phase, tuple(blockers))

    if evidence.get("schemaVersion") != CUTOVER_POLICY_VERSION:
        blockers.append("unsupported_evidence_version")
    generated_at = evidence.get("generatedAt")
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
            blockers.append("live_conformance_evidence_stale")
    except (TypeError, ValueError):
        blockers.append("live_conformance_evidence_timestamp_invalid")

    required_true = (
        "profilePolicyReady",
        "allRequiredCasesPassed",
        "secretScansPassed",
        "temporalReplayPassed",
        "historicalReadsPassed",
        "capacitySingleOwnerPassed",
    )
    blockers.extend(
        f"{key}_required" for key in required_true if evidence.get(key) is not True
    )
    thresholds = evidence.get("thresholds")
    if not isinstance(thresholds, Mapping) or thresholds.get("withinLimits") is not True:
        blockers.append("rollback_threshold_exceeded_or_missing")
    refs = evidence.get("evidenceRefs")
    if not isinstance(refs, list) or not refs or any(
        not isinstance(ref, str) or not ref.strip() for ref in refs
    ):
        blockers.append("independently_resolvable_evidence_refs_required")

    return PromotionDecision(
        not blockers, current_phase, requested_phase, tuple(dict.fromkeys(blockers))
    )


__all__ = [
    "CUTOVER_POLICY_VERSION",
    "MAX_EVIDENCE_AGE_SECONDS",
    "CutoverPhase",
    "PromotionDecision",
    "evaluate_promotion",
]
