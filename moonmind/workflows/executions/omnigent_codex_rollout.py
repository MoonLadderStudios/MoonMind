"""Fail-closed, replay-safe Codex-through-Omnigent rollout policy.

The dynamic decision is made before workflow start and its returned snapshot is
persisted with authored execution input. Workflows must not re-read settings.
MoonLadderStudios/MoonMind#3518.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
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


class CodexCutoverDecision(BaseModel):
    """Immutable selection evidence safe to include in workflow input."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    generation: str
    phase: RolloutPhase
    admitted: bool
    selected_path: Literal["omnigent", "direct", "none"] = Field(alias="selectedPath")
    reason_code: str = Field(alias="reasonCode")
    evidence_ref: str | None = Field(alias="evidenceRef")
    matrix_version: str | None = Field(alias="matrixVersion")


def _parse_evidence(raw: str) -> CodexCutoverEvidence | None:
    try:
        return CodexCutoverEvidence.model_validate(json.loads(raw)) if raw.strip() else None
    except (TypeError, ValueError):
        return None


def decide_codex_path(
    *, feature_flags: object, selection: Literal["automatic", "omnigent", "direct"],
    submission: Literal["create", "edit", "rerun", "schedule", "preset"],
    now: datetime | None = None,
) -> CodexCutoverDecision:
    """Select a path without implicit fallback and with objective promotion gates."""

    phase = getattr(feature_flags, "omnigent_codex_rollout_phase", "internal")
    generation = str(getattr(feature_flags, "omnigent_codex_rollout_generation", "v1"))
    evidence = _parse_evidence(str(getattr(feature_flags, "omnigent_codex_conformance_evidence_json", "")))
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
    )
    kwargs = dict(
        generation=generation, phase=phase,
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
        omnigent_default = False
    if omnigent_default:
        return CodexCutoverDecision(admitted=evidence_valid, selectedPath="omnigent" if evidence_valid else "none", reasonCode="rollout_default" if evidence_valid else "conformance_gate_failed", **kwargs)
    return CodexCutoverDecision(admitted=True, selectedPath="direct", reasonCode="migration_window_default", **kwargs)
