"""Fail-closed, replay-safe Codex-through-Omnigent rollout policy.

The dynamic decision is made before workflow start and its returned snapshot is
persisted with authored execution input. Workflows must not re-read settings.
MoonLadderStudios/MoonMind#3518.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RolloutPhase = Literal[
    "internal", "create_default", "scheduled_default", "broad_default",
    "direct_disabled", "retired",
]


class CodexCutoverEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    generation: str
    matrix_version: str = Field(alias="matrixVersion")
    report_ref: str = Field(alias="reportRef")
    report_sha256: str = Field(alias="reportSha256")
    signature: str
    recorded_at: datetime = Field(alias="recordedAt")
    live_conformance_passed: bool = Field(alias="liveConformancePassed")
    replay_passed: bool = Field(alias="replayPassed")
    historical_reads_passed: bool = Field(alias="historicalReadsPassed")
    secret_violations: int = Field(ge=0, alias="secretViolations")
    readiness_success_ratio: float = Field(ge=0, le=1, alias="readinessSuccessRatio")
    terminal_harvest_success_ratio: float = Field(ge=0, le=1, alias="terminalHarvestSuccessRatio")
    cleanup_failure_ratio: float = Field(ge=0, le=1, alias="cleanupFailureRatio")
    replay_gap_ratio: float = Field(ge=0, le=1, alias="replayGapRatio")
    control_delivery_success_ratio: float = Field(ge=0, le=1, alias="controlDeliverySuccessRatio")
    artifact_completeness_ratio: float = Field(ge=0, le=1, alias="artifactCompletenessRatio")
    checkpoint_success_ratio: float = Field(ge=0, le=1, alias="checkpointSuccessRatio")
    remediation_success_ratio: float = Field(ge=0, le=1, alias="remediationSuccessRatio")
    rag_success_ratio: float = Field(ge=0, le=1, alias="ragSuccessRatio")
    janitor_failure_ratio: float = Field(ge=0, le=1, alias="janitorFailureRatio")
    policy_denials: int = Field(ge=0, alias="policyDenials")
    readiness_denials: int = Field(ge=0, alias="readinessDenials")
    qualified_case_ids: tuple[str, ...] = Field(alias="qualifiedCaseIds")
    host_modes: tuple[Literal["static", "ondemand"], ...] = Field(alias="hostModes")
    architectures: tuple[str, ...]
    image_digests: tuple[str, ...] = Field(alias="imageDigests")


class CodexCutoverDecision(BaseModel):
    """Immutable selection evidence safe to include in workflow input."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    generation: str
    phase: RolloutPhase
    cohort: Literal["internal", "general"]
    admitted: bool
    selected_path: Literal["omnigent", "direct", "none"] = Field(alias="selectedPath")
    reason_code: str = Field(alias="reasonCode")
    evidence_ref: str | None = Field(alias="evidenceRef")
    matrix_version: str | None = Field(alias="matrixVersion")


class CodexCutoverReleaseStatus(BaseModel):
    """Operator projection of the admission gate, without mutable raw evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    generation: str
    phase: RolloutPhase
    promotion_ready: bool = Field(alias="promotionReady")
    reason_code: str = Field(alias="reasonCode")
    evidence_ref: str | None = Field(alias="evidenceRef")
    matrix_version: str | None = Field(alias="matrixVersion")


def _canonical_evidence_payload(evidence: CodexCutoverEvidence) -> bytes:
    payload = evidence.model_dump(by_alias=True, mode="json", exclude={"signature"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_codex_cutover_evidence(
    payload: dict[str, object], *, signing_key: str
) -> dict[str, object]:
    """Sign a protected report projection for ingestion by the admission gate."""

    unsigned = dict(payload)
    unsigned["signature"] = "0" * 64
    evidence = CodexCutoverEvidence.model_validate(unsigned)
    unsigned["signature"] = hmac.new(
        signing_key.encode(), _canonical_evidence_payload(evidence), hashlib.sha256
    ).hexdigest()
    return unsigned


def _parse_evidence(raw: str, *, signing_key: str) -> CodexCutoverEvidence | None:
    try:
        evidence = CodexCutoverEvidence.model_validate(json.loads(raw)) if raw.strip() else None
    except (TypeError, ValueError):
        return None
    if evidence is None or not signing_key:
        return None
    expected = hmac.new(
        signing_key.encode(), _canonical_evidence_payload(evidence), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(evidence.signature, expected):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", evidence.report_sha256):
        return None
    if not evidence.report_ref.startswith(("artifact://", "https://")):
        return None
    if not evidence.qualified_case_ids or not evidence.host_modes:
        return None
    if not evidence.architectures or not evidence.image_digests:
        return None
    if any(not re.fullmatch(r".+@sha256:[0-9a-f]{64}", item) for item in evidence.image_digests):
        return None
    return evidence


def decide_codex_path(
    *, feature_flags: object, selection: Literal["automatic", "omnigent", "direct"],
    submission: Literal["create", "edit", "rerun", "schedule", "preset"],
    cohort: Literal["internal", "general"] = "general",
    now: datetime | None = None,
) -> CodexCutoverDecision:
    """Select a path without implicit fallback and with objective promotion gates."""

    phase = getattr(feature_flags, "omnigent_codex_rollout_phase", "internal")
    generation = str(getattr(feature_flags, "omnigent_codex_rollout_generation", "v1"))
    evidence = _parse_evidence(
        str(getattr(feature_flags, "omnigent_codex_conformance_evidence_json", "")),
        signing_key=str(
            getattr(feature_flags, "omnigent_codex_conformance_signing_key", "")
        ),
    )
    current = now or datetime.now(timezone.utc)
    max_age = int(getattr(feature_flags, "omnigent_codex_conformance_max_age_hours", 168))
    evidence_valid = bool(
        evidence
        and evidence.generation == generation
        and evidence.recorded_at >= current - timedelta(hours=max_age)
        and evidence.live_conformance_passed
        and evidence.replay_passed
        and evidence.historical_reads_passed
        and evidence.secret_violations == 0
        and evidence.readiness_success_ratio >= .99
        and evidence.terminal_harvest_success_ratio >= .99
        and evidence.cleanup_failure_ratio <= .01
        and evidence.replay_gap_ratio == 0
        and evidence.control_delivery_success_ratio >= .99
        and evidence.artifact_completeness_ratio >= .99
        and evidence.checkpoint_success_ratio >= .99
        and evidence.remediation_success_ratio >= .99
        and evidence.rag_success_ratio >= .99
        and evidence.janitor_failure_ratio <= .01
    )
    kwargs = dict(
        generation=generation, phase=phase, cohort=cohort,
        evidenceRef=evidence.report_ref if evidence else None,
        matrixVersion=evidence.matrix_version if evidence else None,
    )
    if selection == "omnigent":
        return CodexCutoverDecision(admitted=evidence_valid, selectedPath="omnigent" if evidence_valid else "none", reasonCode="explicit_omnigent" if evidence_valid else "conformance_gate_failed", **kwargs)
    if selection == "direct":
        allowed = phase not in {"direct_disabled", "retired"}
        return CodexCutoverDecision(admitted=allowed, selectedPath="direct" if allowed else "none", reasonCode="explicit_direct" if allowed else "direct_launch_disabled", **kwargs)

    omnigent_default = phase in {"create_default", "scheduled_default", "broad_default", "direct_disabled", "retired"}
    if submission in {"schedule", "preset"} and phase == "create_default":
        omnigent_default = False
    if phase == "internal":
        omnigent_default = cohort == "internal"
    if omnigent_default:
        return CodexCutoverDecision(admitted=evidence_valid, selectedPath="omnigent" if evidence_valid else "none", reasonCode="rollout_default" if evidence_valid else "conformance_gate_failed", **kwargs)
    return CodexCutoverDecision(admitted=True, selectedPath="direct", reasonCode="migration_window_default", **kwargs)


def project_codex_release_status(
    *, feature_flags: object, now: datetime | None = None
) -> CodexCutoverReleaseStatus:
    """Project the exact gate used by explicit Omnigent admission."""

    decision = decide_codex_path(
        feature_flags=feature_flags,
        selection="omnigent",
        submission="create",
        cohort="general",
        now=now,
    )
    return CodexCutoverReleaseStatus(
        generation=decision.generation,
        phase=decision.phase,
        promotionReady=decision.admitted,
        reasonCode=decision.reason_code,
        evidenceRef=decision.evidence_ref,
        matrixVersion=decision.matrix_version,
    )
