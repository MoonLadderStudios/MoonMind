"""Compact Temporal contracts for the Omnigent session supervisor.

Source: MoonLadderStudios/MoonMind#3705.

The contracts in this module are deliberately reference-only. Provider request
bodies, credentials, transcripts, host paths, and artifact bodies are resolved
inside bounded Activities and never enter ``MoonMind.OmnigentSession`` history.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from moonmind.schemas.agent_runtime_models import AgentRunResult


OMNIGENT_SESSION_WORKFLOW_SCHEMA_VERSION = "omnigent-session-workflow/v1"
OMNIGENT_SESSION_COMPATIBILITY_VERSION = "v1"
OMNIGENT_SESSION_FEATURE_GENERATION = "omnigent-session-v1"

OmnigentSessionFailureStatus = Literal[
    "integration_unavailable",
    "execution_failed",
    "delivery_unknown",
    "reconciliation_quarantined",
    "cleanup_incomplete",
]

_ARTIFACT_REF_PATTERN = re.compile(
    r"^(?:art(?:ifact)?[_:]|artifact://)[A-Za-z0-9_.:/-]+$"
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class _OmnigentSessionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


def _require_compact_identifier(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > 1024:
        raise ValueError(f"{field_name} exceeds the compact history limit")
    return normalized


def _require_artifact_ref(value: str, *, field_name: str) -> str:
    normalized = _require_compact_identifier(value, field_name=field_name)
    if normalized.startswith(
        ("/", "./", "../", "~")
    ) or not _ARTIFACT_REF_PATTERN.match(normalized):
        raise ValueError(f"{field_name} must be an opaque artifact reference")
    return normalized


class OmnigentSessionContinueAsNewState(_OmnigentSessionModel):
    """Minimum replay-safe state carried across a history rollover."""

    continue_as_new_count: int = Field(0, alias="continueAsNewCount", ge=0)
    decision_count: int = Field(0, alias="decisionCount", ge=0)
    observation_count: int = Field(0, alias="observationCount", ge=0)
    turn_attempt_count: int = Field(1, alias="turnAttemptCount", ge=1)
    last_session_revision: int | None = Field(None, alias="lastSessionRevision", ge=1)
    last_event_cursor: str | None = Field(None, alias="lastEventCursor", max_length=255)
    last_snapshot_frontier: str | None = Field(
        None, alias="lastSnapshotFrontier", max_length=255
    )
    terminal_result_ref: str | None = Field(None, alias="terminalResultRef")
    session_started_at: datetime | None = Field(None, alias="sessionStartedAt")

    @field_validator("terminal_result_ref")
    @classmethod
    def _validate_terminal_result_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_artifact_ref(value, field_name="terminalResultRef")


class OmnigentSessionWorkflowInput(_OmnigentSessionModel):
    """Immutable authority plus a bounded Continue-As-New summary."""

    schema_version: Literal[OMNIGENT_SESSION_WORKFLOW_SCHEMA_VERSION] = Field(
        OMNIGENT_SESSION_WORKFLOW_SCHEMA_VERSION, alias="schemaVersion"
    )
    session_id: str = Field(alias="sessionId", min_length=1, max_length=255)
    compiled_execution_intent_ref: str = Field(
        alias="compiledExecutionIntentRef", min_length=1, max_length=1024
    )
    compiled_execution_intent_digest: str = Field(
        alias="compiledExecutionIntentDigest", min_length=1, max_length=128
    )
    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=255)
    step_execution_id: str = Field(
        alias="stepExecutionId", min_length=1, max_length=255
    )
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=255)
    initial_turn_attempt_id: str = Field(
        alias="initialTurnAttemptId", min_length=1, max_length=255
    )
    admitted_feature_generation: Literal[OMNIGENT_SESSION_FEATURE_GENERATION] = Field(
        OMNIGENT_SESSION_FEATURE_GENERATION, alias="admittedFeatureGeneration"
    )
    compatibility_version: Literal[OMNIGENT_SESSION_COMPATIBILITY_VERSION] = Field(
        OMNIGENT_SESSION_COMPATIBILITY_VERSION, alias="compatibilityVersion"
    )
    resume_state: OmnigentSessionContinueAsNewState | None = Field(
        None, alias="resumeState"
    )

    @field_validator(
        "session_id",
        "workflow_id",
        "step_execution_id",
        "agent_run_id",
        "initial_turn_attempt_id",
    )
    @classmethod
    def _validate_identifier(cls, value: str, info: Any) -> str:
        return _require_compact_identifier(value, field_name=info.field_name)

    @field_validator("compiled_execution_intent_ref")
    @classmethod
    def _validate_intent_ref(cls, value: str) -> str:
        return _require_artifact_ref(value, field_name="compiledExecutionIntentRef")

    @field_validator("compiled_execution_intent_digest")
    @classmethod
    def _validate_intent_digest(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.match(normalized):
            raise ValueError("compiledExecutionIntentDigest must be a sha256 digest")
        return normalized


class OmnigentSessionSignal(_OmnigentSessionModel):
    """Safe signal/update payload; all content is an identifier or durable ref."""

    request_id: str = Field(alias="requestId", min_length=1, max_length=255)
    observation_ref: str | None = Field(None, alias="observationRef")
    instruction_ref: str | None = Field(None, alias="instructionRef")
    turn_attempt_id: str | None = Field(None, alias="turnAttemptId", max_length=255)
    reason_code: str | None = Field(None, alias="reasonCode", max_length=128)
    provider_epoch: str | None = Field(None, alias="providerEpoch", max_length=255)
    observed_at: datetime | None = Field(None, alias="observedAt")

    @field_validator("observation_ref", "instruction_ref")
    @classmethod
    def _validate_optional_ref(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_artifact_ref(value, field_name=info.field_name)


class OmnigentResolveIntentRequest(_OmnigentSessionModel):
    """AgentRun-to-Activity handoff used before the compact child starts."""

    request: dict[str, Any]
    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=255)
    step_execution_id: str = Field(
        alias="stepExecutionId", min_length=1, max_length=255
    )
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=255)
    admitted_feature_generation: Literal[OMNIGENT_SESSION_FEATURE_GENERATION] = Field(
        OMNIGENT_SESSION_FEATURE_GENERATION,
        alias="admittedFeatureGeneration",
    )
    compatibility_version: Literal[OMNIGENT_SESSION_COMPATIBILITY_VERSION] = Field(
        OMNIGENT_SESSION_COMPATIBILITY_VERSION, alias="compatibilityVersion"
    )


class OmnigentSessionAdmissionRequest(_OmnigentSessionModel):
    """Compact identities evaluated before a new session child is launched."""

    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=255)
    step_execution_id: str = Field(
        alias="stepExecutionId", min_length=1, max_length=255
    )
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=255)
    execution_profile_ref: str = Field(
        alias="executionProfileRef", min_length=1, max_length=255
    )

    @field_validator(
        "workflow_id", "step_execution_id", "agent_run_id", "execution_profile_ref"
    )
    @classmethod
    def _validate_admission_identifier(cls, value: str, info: Any) -> str:
        return _require_compact_identifier(value, field_name=info.field_name)


class OmnigentSessionAdmissionDecision(_OmnigentSessionModel):
    """Frozen replay authority for one new-session admission decision."""

    admitted: bool
    reason_code: Literal[
        "enabled",
        "canary_selected",
        "new_selection_disabled",
        "canary_owner_not_allowlisted",
        "execution_profile_not_allowlisted",
        "feature_generation_mismatch",
    ] = Field(alias="reasonCode")
    admission_mode: Literal["disabled", "canary", "enabled"] = Field(
        alias="admissionMode"
    )
    admitted_feature_generation: Literal[OMNIGENT_SESSION_FEATURE_GENERATION] = (
        Field(
            OMNIGENT_SESSION_FEATURE_GENERATION,
            alias="admittedFeatureGeneration",
        )
    )


class OmnigentFailureAuthorityRequest(_OmnigentSessionModel):
    """Immutable child authority used to recover the current session fence."""

    session_id: str = Field(alias="sessionId", min_length=1, max_length=255)
    compiled_execution_intent_ref: str = Field(
        alias="compiledExecutionIntentRef", min_length=1, max_length=1024
    )
    compiled_execution_intent_digest: str = Field(
        alias="compiledExecutionIntentDigest", min_length=1, max_length=128
    )
    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=255)
    step_execution_id: str = Field(
        alias="stepExecutionId", min_length=1, max_length=255
    )
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=255)

    @field_validator("compiled_execution_intent_ref")
    @classmethod
    def _validate_failure_intent_ref(cls, value: str) -> str:
        return _require_artifact_ref(value, field_name="compiledExecutionIntentRef")

    @field_validator("compiled_execution_intent_digest")
    @classmethod
    def _validate_failure_intent_digest(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.match(normalized):
            raise ValueError("compiledExecutionIntentDigest must be a sha256 digest")
        return normalized


class OmnigentSessionActivityRequest(_OmnigentSessionModel):
    """Reference-only request shared by bounded supervisor Activities."""

    session_id: str = Field(alias="sessionId", min_length=1, max_length=255)
    compiled_execution_intent_ref: str = Field(
        alias="compiledExecutionIntentRef", min_length=1, max_length=1024
    )
    compiled_execution_intent_digest: str = Field(
        alias="compiledExecutionIntentDigest", min_length=1, max_length=128
    )
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    fencing_generation: int = Field(alias="fencingGeneration", ge=0)
    decision_id: str | None = Field(None, alias="decisionId", max_length=255)
    command_id: str | None = Field(None, alias="commandId", max_length=255)
    turn_attempt_id: str | None = Field(None, alias="turnAttemptId", max_length=255)
    terminal_outcome: str | None = Field(None, alias="terminalOutcome", max_length=64)

    @field_validator("compiled_execution_intent_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_artifact_ref(value, field_name="compiledExecutionIntentRef")

    @field_validator("compiled_execution_intent_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _DIGEST_PATTERN.match(normalized):
            raise ValueError("compiledExecutionIntentDigest must be a sha256 digest")
        return normalized


class OmnigentPersistDecisionRequest(OmnigentSessionActivityRequest):
    decision: dict[str, Any]


class OmnigentPersistSignalsRequest(OmnigentSessionActivityRequest):
    signals: tuple[dict[str, Any], ...] = ()


class OmnigentPersistFailureRequest(OmnigentSessionActivityRequest):
    """Bounded failure evidence; exception prose never enters history."""

    status: OmnigentSessionFailureStatus
    failed_activity: str = Field(alias="failedActivity", min_length=1, max_length=128)
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=128)


class OmnigentSessionTerminalResult(_OmnigentSessionModel):
    """Compact durable result returned to AgentRun after cleanup/release."""

    status: Literal[
        "integration_unavailable",
        "execution_failed",
        "delivery_unknown",
        "reconciliation_quarantined",
        "cleanup_incomplete",
        "timed_out",
        "canceled",
        "completed",
    ]
    result_ref: str | None = Field(None, alias="resultRef")
    result: AgentRunResult

    @field_validator("result")
    @classmethod
    def _validate_compact_result(cls, value: AgentRunResult) -> AgentRunResult:
        allowed_metadata = {
            "canonicalSessionId",
            "providerSessionRef",
            "chatBindingId",
            "terminalState",
            "publicationEvidenceRef",
            "omnigentSessionStatus",
            "reasonCode",
            "cleanupEvidenceRef",
            "workflowFailureEvidenceRef",
            "cleanupOwner",
            "janitorRequired",
            "primaryOmnigentSessionStatus",
        }
        unknown_metadata = sorted(set(value.metadata) - allowed_metadata)
        if unknown_metadata:
            raise ValueError(
                "Omnigent terminal result metadata must be reference-only; "
                f"unsupported keys: {unknown_metadata}"
            )
        for ref in value.output_refs:
            _require_artifact_ref(ref, field_name="result.outputRefs[]")
        if value.diagnostics_ref is not None:
            _require_artifact_ref(
                value.diagnostics_ref, field_name="result.diagnosticsRef"
            )
        if any(
            not isinstance(metric, (bool, int, float)) and metric is not None
            for metric in value.metrics.values()
        ):
            raise ValueError("Omnigent terminal result metrics must be scalar")
        encoded = json.dumps(
            value.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 32 * 1024:
            raise ValueError(
                "Omnigent terminal result exceeds the compact history limit"
            )
        return value

    @field_validator("result_ref")
    @classmethod
    def _validate_result_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_artifact_ref(value, field_name="resultRef")


__all__ = [
    "OMNIGENT_SESSION_WORKFLOW_SCHEMA_VERSION",
    "OMNIGENT_SESSION_COMPATIBILITY_VERSION",
    "OMNIGENT_SESSION_FEATURE_GENERATION",
    "OmnigentSessionFailureStatus",
    "OmnigentSessionContinueAsNewState",
    "OmnigentSessionWorkflowInput",
    "OmnigentSessionSignal",
    "OmnigentResolveIntentRequest",
    "OmnigentSessionAdmissionRequest",
    "OmnigentSessionAdmissionDecision",
    "OmnigentFailureAuthorityRequest",
    "OmnigentSessionActivityRequest",
    "OmnigentPersistDecisionRequest",
    "OmnigentPersistSignalsRequest",
    "OmnigentPersistFailureRequest",
    "OmnigentSessionTerminalResult",
]
