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
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from moonmind.omnigent.conformance import (
    PROFILE_SHA256,
    PROFILE_VERSION,
    ConformanceContractError,
    require_pinned_images,
)

CUTOVER_POLICY_VERSION = "moonmind.codex-omnigent-cutover/v1"
# Phase 6 is a build property, not a live-artifact assertion. The retirement
# change that actually removes direct launch/UI/configuration ownership must set
# this to its versioned removal manifest only after absence guards pass.
DIRECT_LAUNCH_REMOVAL_VERSION: str | None = None
MAX_EVIDENCE_AGE_SECONDS = 7 * 24 * 60 * 60
REQUIRED_TELEMETRY_GROUPS = (
    "launchReadiness",
    "stageLatency",
    "reconnectReplay",
    "controls",
    "failures",
    "artifactCapture",
    "checkpointResumeBranch",
    "remediationRag",
    "cleanupJanitor",
    "runtimeSelection",
    "secretRedaction",
    "policyReadinessDenials",
)


class CutoverPhase(IntEnum):
    OPT_IN = 1
    CREATE_DEFAULT = 2
    SCHEDULE_DEFAULT = 3
    BROAD_DEFAULT = 4
    DIRECT_LAUNCH_DISABLED = 5
    DIRECT_LAUNCH_REMOVED = 6


def configured_phase(*, env: Mapping[str, Any] | None = None) -> CutoverPhase:
    """Resolve the versioned deployment phase; invalid values fail closed."""

    values = os.environ if env is None else env
    raw = str(values.get("MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE", "opt_in"))
    normalized = raw.strip().upper().replace("-", "_")
    try:
        return CutoverPhase[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex Omnigent cutover phase: {raw!r}") from exc


def deployed_phase(*, env: Mapping[str, Any] | None = None) -> CutoverPhase:
    """Resolve the durable phase currently deployed by the operator."""

    values = os.environ if env is None else env
    raw = str(values.get("MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE", "opt_in"))
    normalized = raw.strip().upper().replace("-", "_")
    try:
        return CutoverPhase[normalized]
    except KeyError as exc:
        raise ValueError(
            f"unsupported deployed Codex Omnigent cutover phase: {raw!r}"
        ) from exc


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


@dataclass(frozen=True, slots=True)
class EffectivePhase:
    """Authoritative, fail-closed release status used by every launch boundary."""

    configured_phase: CutoverPhase
    deployed_phase: CutoverPhase
    phase: CutoverPhase
    evidence_ref: str | None
    evidence: Mapping[str, Any] | None
    blockers: tuple[str, ...]
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        evidence = self.evidence or {}
        images = evidence.get("images")
        architectures = evidence.get("architectures")
        thresholds = evidence.get("thresholds")
        evidence_refs = evidence.get("evidenceRefs")
        evidence_sha256 = (
            hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if evidence
            else None
        )
        generated_at = evidence.get("generatedAt")
        expires_at: str | None = None
        if isinstance(generated_at, str):
            try:
                generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                expires_at = datetime.fromtimestamp(
                    generated.timestamp() + MAX_EVIDENCE_AGE_SECONDS,
                    tz=timezone.utc,
                ).isoformat()
            except ValueError:
                pass
        return {
            "policyVersion": self.policy_version,
            "configuredPhase": self.configured_phase.name.lower(),
            "deployedPhase": self.deployed_phase.name.lower(),
            "phase": self.phase.name.lower(),
            "promotionAllowed": not self.blockers,
            "evidenceRef": self.evidence_ref,
            "evidenceSha256": evidence_sha256,
            "generatedAt": generated_at,
            "expiresAt": expires_at,
            "profileVersion": evidence.get("profileVersion"),
            "profileSha256": evidence.get("profileSha256"),
            "images": dict(images) if isinstance(images, Mapping) else {},
            "architectures": (
                list(architectures) if isinstance(architectures, list) else []
            ),
            "thresholds": (
                dict(thresholds) if isinstance(thresholds, Mapping) else {}
            ),
            "evidenceRefs": (
                list(evidence_refs) if isinstance(evidence_refs, list) else []
            ),
            "blockers": list(self.blockers),
            "directLaunchAllowed": self.phase
            < CutoverPhase.DIRECT_LAUNCH_DISABLED,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    """Immutable evidence explaining one cutover-aware runtime choice."""

    runtime_id: str
    authored: bool
    fallback_reason: str | None
    phase: CutoverPhase
    evidence_ref: str | None = None
    evidence_sha256: str | None = None
    policy_version: str = CUTOVER_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "phase": self.phase.name.lower(),
            "runtimeId": self.runtime_id,
            "authored": self.authored,
            "fallbackReason": self.fallback_reason,
            "evidenceRef": self.evidence_ref,
            "evidenceSha256": self.evidence_sha256,
        }


def select_runtime(
    *,
    authored_runtime: str | None,
    configured_default: str,
    phase: CutoverPhase,
    submission_kind: str = "create",
    release_status: EffectivePhase | None = None,
) -> RuntimeSelection:
    """Apply rollout defaults without ever rewriting an explicit selection.

    Create/edit/rerun defaults advance at phase 2; schedule and preset defaults
    advance at phase 3.  Explicit direct launch is rejected from phase 5.  This
    helper never performs automatic fallback: callers must persist the returned
    evidence on the run before launch.
    """

    explicit = str(authored_runtime or "").strip().lower()
    if explicit:
        if explicit == "codex_cli" and phase >= CutoverPhase.DIRECT_LAUNCH_DISABLED:
            raise ValueError("codex_direct_launch_disabled_by_cutover_phase")
        return RuntimeSelection(
            explicit,
            True,
            None,
            phase,
            release_status.evidence_ref if release_status else None,
            release_status.as_dict()["evidenceSha256"] if release_status else None,
        )

    default = str(configured_default or "codex_cli").strip().lower()
    threshold = (
        CutoverPhase.SCHEDULE_DEFAULT
        if submission_kind in {"schedule", "preset"}
        else CutoverPhase.CREATE_DEFAULT
    )
    selected = "omnigent" if default == "codex_cli" and phase >= threshold else default
    return RuntimeSelection(
        selected,
        False,
        None,
        phase,
        release_status.evidence_ref if release_status else None,
        release_status.as_dict()["evidenceSha256"] if release_status else None,
    )


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
    threshold_results = (
        thresholds.get("results") if isinstance(thresholds, Mapping) else None
    )
    if (
        not isinstance(thresholds, Mapping)
        or thresholds.get("withinLimits") is not True
        or not isinstance(threshold_results, Mapping)
        or not threshold_results
        or any(result is not True for result in threshold_results.values())
    ):
        blockers.append("rollback_threshold_exceeded_or_missing")
    if (
        evidence.get("profileVersion") != PROFILE_VERSION
        or evidence.get("profileSha256") != PROFILE_SHA256
    ):
        blockers.append("canonical_conformance_profile_required")
    images = evidence.get("images")
    try:
        if not isinstance(images, Mapping):
            raise ConformanceContractError("release images are required")
        require_pinned_images(images)
    except ConformanceContractError:
        blockers.append("immutable_release_images_required")
    architectures = evidence.get("architectures")
    if not isinstance(architectures, list) or not architectures or any(
        not isinstance(item, str) or not item.strip() for item in architectures
    ):
        blockers.append("tested_architectures_required")
    telemetry = evidence.get("telemetry")
    if not isinstance(telemetry, Mapping) or any(
        not isinstance(telemetry.get(group), Mapping)
        or not telemetry[group]
        for group in REQUIRED_TELEMETRY_GROUPS
    ):
        blockers.append("migration_telemetry_required")
    refs = evidence.get("evidenceRefs")
    if not isinstance(refs, list) or not refs or any(
        not isinstance(ref, str) or not ref.strip() for ref in refs
    ):
        blockers.append("independently_resolvable_evidence_refs_required")
    if requested_phase is CutoverPhase.DIRECT_LAUNCH_REMOVED:
        if DIRECT_LAUNCH_REMOVAL_VERSION is None:
            blockers.append("direct_launch_retirement_not_built")
        retirement_assertions = (
            "directLaunchCodeRemoved",
            "directLaunchUiRemoved",
            "directLaunchConfigRemoved",
            "duplicateCapacityOwnershipRemoved",
        )
        blockers.extend(
            f"{key}_required"
            for key in retirement_assertions
            if evidence.get(key) is not True
        )
        retirement_refs = evidence.get("retirementEvidenceRefs")
        if not isinstance(retirement_refs, list) or not retirement_refs or any(
            not isinstance(ref, str) or not ref.strip() for ref in retirement_refs
        ):
            blockers.append("retirement_evidence_refs_required")

    return PromotionDecision(
        not blockers, current_phase, requested_phase, tuple(dict.fromkeys(blockers))
    )


def _evidence_path(ref: str) -> Path:
    """Resolve only deployment-local evidence; remote refs are not launch authority."""

    parsed = urlparse(ref)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("conformance_evidence_ref_not_local")
        return Path(unquote(parsed.path))
    if parsed.scheme:
        raise ValueError("conformance_evidence_ref_not_local")
    return Path(ref)


def effective_phase(
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> EffectivePhase:
    """Load protected release evidence and authorize the configured phase.

    Phase one is the immutable fail-closed baseline. Later phases require a
    local evidence document mounted into the API/worker deployment. Merely
    setting the desired phase never changes execution defaults.
    """

    values = os.environ if env is None else env
    requested = configured_phase(env=values)
    current = deployed_phase(env=values)
    raw_ref = str(
        values.get("MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF", "")
    ).strip()
    ref = raw_ref or None
    if requested <= current:
        return EffectivePhase(requested, current, requested, ref, None, ())

    blockers: list[str] = []
    evidence: Mapping[str, Any] | None = None
    if not ref:
        blockers.append("live_conformance_evidence_missing")
    else:
        try:
            payload = json.loads(_evidence_path(ref).read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("conformance_evidence_not_object")
            evidence = payload
        except FileNotFoundError:
            blockers.append("live_conformance_evidence_unreadable")
        except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
            blockers.append(str(exc) or "live_conformance_evidence_unreadable")

    if evidence is not None:
        authorized = str(evidence.get("authorizedPhase") or "").strip().upper()
        if authorized != requested.name:
            blockers.append("evidence_authorized_phase_mismatch")
        evidence_current = str(evidence.get("currentPhase") or "").strip().upper()
        if evidence_current != current.name:
            blockers.append("evidence_current_phase_mismatch")
        decision = evaluate_promotion(
            current_phase=current,
            requested_phase=requested,
            evidence=evidence,
            now=now,
        )
        blockers.extend(decision.blockers)

    unique_blockers = tuple(dict.fromkeys(blockers))
    return EffectivePhase(
        configured_phase=requested,
        deployed_phase=current,
        phase=current if unique_blockers else requested,
        evidence_ref=ref,
        evidence=evidence,
        blockers=unique_blockers,
    )


__all__ = [
    "CUTOVER_POLICY_VERSION",
    "DIRECT_LAUNCH_REMOVAL_VERSION",
    "MAX_EVIDENCE_AGE_SECONDS",
    "REQUIRED_TELEMETRY_GROUPS",
    "CutoverPhase",
    "EffectivePhase",
    "configured_phase",
    "deployed_phase",
    "effective_phase",
    "PromotionDecision",
    "RuntimeSelection",
    "evaluate_promotion",
    "select_runtime",
]
