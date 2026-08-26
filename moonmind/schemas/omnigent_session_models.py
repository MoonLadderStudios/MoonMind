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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.temporal_payload_policy import (
    validate_compact_temporal_mapping,
)


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
    omnigent_execution_plan: OmnigentExecutionPlanBinding | None = Field(
        None,
        alias="omnigentExecutionPlan",
        exclude_if=lambda value: value is None,
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

    omnigent_execution_plan: OmnigentExecutionPlanBinding | None = Field(
        None, alias="omnigentExecutionPlan"
    )
    # Replay-only field for histories scheduled before the compact plan-bound
    # handoff. New AgentRun histories never populate it.
    request: dict[str, Any] | None = None
    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=255)
    step_execution_id: str = Field(
        alias="stepExecutionId", min_length=1, max_length=255
    )
    agent_run_id: str = Field(alias="agentRunId", min_length=1, max_length=255)
    logical_step_id: str | None = Field(
        None, alias="logicalStepId", min_length=1, max_length=255
    )
    execution_instruction_ref: str | None = Field(
        None, alias="executionInstructionRef", min_length=1, max_length=65536
    )
    execution_instruction_digest: str | None = Field(
        None, alias="executionInstructionDigest"
    )
    execution_input_refs: list[str] = Field(
        default_factory=list,
        alias="executionInputRefs",
        max_length=128,
        exclude_if=lambda value: not value,
    )
    execution_input_refs_digest: str | None = Field(
        None, alias="executionInputRefsDigest"
    )
    admitted_feature_generation: Literal[OMNIGENT_SESSION_FEATURE_GENERATION] = Field(
        OMNIGENT_SESSION_FEATURE_GENERATION,
        alias="admittedFeatureGeneration",
    )
    compatibility_version: Literal[OMNIGENT_SESSION_COMPATIBILITY_VERSION] = Field(
        OMNIGENT_SESSION_COMPATIBILITY_VERSION, alias="compatibilityVersion"
    )

    @model_validator(mode="after")
    def require_one_authority(self) -> "OmnigentResolveIntentRequest":
        if (self.omnigent_execution_plan is None) == (self.request is None):
            raise ValueError(
                "resolve intent requires exactly one persisted plan authority"
            )
        if self.request is not None and any(
            (
                self.execution_instruction_ref,
                self.execution_instruction_digest,
                self.execution_input_refs,
                self.execution_input_refs_digest,
            )
        ):
            raise ValueError(
                "legacy request authority cannot carry compact execution inputs"
            )
        if (self.execution_instruction_ref is None) != (
            self.execution_instruction_digest is None
        ):
            raise ValueError(
                "execution instruction ref and digest must be recorded atomically"
            )
        if bool(self.execution_input_refs) != bool(
            self.execution_input_refs_digest
        ):
            raise ValueError(
                "execution input refs and digest must be recorded atomically"
            )
        for digest in (
            self.execution_instruction_digest,
            self.execution_input_refs_digest,
        ):
            if digest is not None and not _DIGEST_PATTERN.match(digest):
                raise ValueError("compact execution input digest must be sha256")
        self.execution_input_refs = [
            _require_compact_identifier(item, field_name="executionInputRefs[]")
            for item in self.execution_input_refs
        ]
        return self


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
    omnigent_execution_plan: OmnigentExecutionPlanBinding | None = Field(
        None,
        alias="omnigentExecutionPlan",
        exclude_if=lambda value: value is None,
    )
    execution_plan_ref: str | None = Field(
        None,
        alias="executionPlanRef",
        min_length=1,
        max_length=255,
        exclude_if=lambda value: value is None,
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
        "realizer_managed_lifecycle",
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
    execution_realizer_ref: str | None = Field(
        None, alias="executionRealizerRef", max_length=255
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
    omnigent_execution_plan: OmnigentExecutionPlanBinding | None = Field(
        None,
        alias="omnigentExecutionPlan",
        exclude_if=lambda value: value is None,
    )
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    fencing_generation: int = Field(alias="fencingGeneration", ge=0)
    runtime_binding_ref: str | None = Field(
        None, alias="runtimeBindingRef", max_length=255
    )
    runtime_binding_revision: int | None = Field(
        None, alias="runtimeBindingRevision", ge=1
    )
    runtime_binding_fencing_generation: int | None = Field(
        None, alias="runtimeBindingFencingGeneration", ge=1
    )
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

    @model_validator(mode="after")
    def _validate_runtime_binding_authority(
        self,
    ) -> "OmnigentSessionActivityRequest":
        authority = (
            self.runtime_binding_ref,
            self.runtime_binding_revision,
            self.runtime_binding_fencing_generation,
        )
        if any(value is not None for value in authority) and not all(
            value is not None for value in authority
        ):
            raise ValueError(
                "runtime binding ref, revision, and fencing generation "
                "must be recorded atomically"
            )
        if self.runtime_binding_ref is not None and not (
            self.runtime_binding_ref.startswith(
                "omnigent-runtime-binding:sha256:"
            )
        ):
            raise ValueError("runtimeBindingRef is invalid")
        return self


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
            "executionPlanRef",
            "executionPlanDigest",
            "runtimeBindingRef",
            "runtimeBindingRevision",
            "runtimeBindingFencingGeneration",
            "runtimeBindingState",
            "publicationEvidenceRef",
            "externalStateRef",
            "omnigentCheckpointCapture",
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
                "Omnigent terminal result metadata must be reference-only or "
                "bounded checkpoint authority; "
                f"unsupported keys: {unknown_metadata}"
            )
        plan_ref = value.metadata.get("executionPlanRef")
        plan_digest = value.metadata.get("executionPlanDigest")
        if (plan_ref is None) != (plan_digest is None):
            raise ValueError(
                "Omnigent terminal plan ref and digest must be recorded atomically"
            )
        if plan_ref is not None:
            prefix = "omnigent-execution-plan:sha256:"
            suffix = str(plan_ref).removeprefix(prefix)
            if not str(plan_ref).startswith(prefix) or not re.fullmatch(
                r"[0-9a-f]{64}", suffix
            ):
                raise ValueError("result.metadata.executionPlanRef is invalid")
            if plan_digest != f"sha256:{suffix}":
                raise ValueError(
                    "result.metadata.executionPlanDigest must match executionPlanRef"
                )
        runtime_authority = (
            value.metadata.get("runtimeBindingRef"),
            value.metadata.get("runtimeBindingRevision"),
            value.metadata.get("runtimeBindingFencingGeneration"),
            value.metadata.get("runtimeBindingState"),
        )
        if any(item is not None for item in runtime_authority) and not all(
            item is not None for item in runtime_authority
        ):
            raise ValueError(
                "Omnigent terminal runtime binding authority must be recorded atomically"
            )
        if all(item is not None for item in runtime_authority):
            runtime_ref, revision, fencing_generation, state = runtime_authority
            prefix = "omnigent-runtime-binding:sha256:"
            suffix = str(runtime_ref).removeprefix(prefix)
            if not str(runtime_ref).startswith(prefix) or not re.fullmatch(
                r"[0-9a-f]{64}", suffix
            ):
                raise ValueError("result.metadata.runtimeBindingRef is invalid")
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 1
                or isinstance(fencing_generation, bool)
                or not isinstance(fencing_generation, int)
                or fencing_generation < 1
            ):
                raise ValueError(
                    "Omnigent terminal runtime binding counters must be positive integers"
                )
            if state not in {
                "credentials_acquired",
                "host_attested",
                "session_bound",
                "cleanup_complete",
            }:
                raise ValueError("result.metadata.runtimeBindingState is invalid")
        for field_name in (
            "publicationEvidenceRef",
            "externalStateRef",
            "cleanupEvidenceRef",
            "workflowFailureEvidenceRef",
        ):
            ref = value.metadata.get(field_name)
            if ref is not None:
                _require_artifact_ref(ref, field_name=f"result.metadata.{field_name}")
        checkpoint_capture = value.metadata.get("omnigentCheckpointCapture")
        if checkpoint_capture is not None:
            validate_compact_temporal_mapping(
                checkpoint_capture,
                field_name="result.metadata.omnigentCheckpointCapture",
            )
            encoded_capture = json.dumps(
                checkpoint_capture,
                sort_keys=True,
                separators=(",", ":"),
            )
            forbidden = (
                "/var/run/docker.sock",
                "providerPayload",
                "secretBody",
                "accessToken",
                "refreshToken",
            )
            if any(value in encoded_capture for value in forbidden):
                raise ValueError(
                    "Omnigent checkpoint authority contains forbidden runtime data"
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
