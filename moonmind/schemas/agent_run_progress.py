"""One typed AgentRun product-progress projection with ordering and recovery.

MoonLadderStudios/MoonMind#1088 ([Omnigent transition]).

``agent_run_progress`` is the sole normal AgentRun product-progress
projection to UserWorkflow for new histories. It is not an Omnigent event
stream, lease record, runtime binding, or cleanup command. The awaited child
completion and the validated ``AgentRunResult`` remain terminal authority.

Design summary (see the issue contract):

* Strict versioned compact schema in the runtime/Temporal schema layer. The
  canonical value vocabulary is ``AgentRunState`` (reused, not redefined);
  the ``AgentRunStatus`` Pydantic response is never copied into the payload.
* One deterministic emitter (child side) and one parent reducer. Each
  logical revision binds to exactly one immutable payload digest. Same
  revision/same digest is a duplicate; same revision/different digest is a
  conflict that cannot overwrite accepted state; older revisions are
  ignored. Gaps need no replay: this is a latest-state projection.
* Revisions order within an authenticated/validated logical source
  generation, never by wall-clock timestamp or lexical UUID. Successor
  run/attempt identity is established from the parent's child lifecycle
  before its updates are accepted. Accepted source/revision/digest persist
  through parent Continue-As-New. Old-run messages cannot reopen a
  replacement or a terminal result.
* Cut over through the named patch/frozen generation
  :data:`AGENT_RUN_PROGRESS_PATCH_ID`. New histories apply only the new
  lifecycle projection; old histories retain the legacy ``child_state_changed``
  behavior. Compatibility handlers stay classified under #3712/#3835.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas._validation import require_non_blank
from moonmind.schemas.agent_runtime_models import AgentRunState

# ---------------------------------------------------------------------------
# Identity and versioning
# ---------------------------------------------------------------------------

#: Strict schema version carried on every projection payload.
AGENT_RUN_PROGRESS_SCHEMA_VERSION = "agent-run-progress/v1"

#: Named patch / frozen generation gating the cutover. New histories apply
#: only the new lifecycle projection; old histories retain required legacy
#: behavior (``child_state_changed``).
AGENT_RUN_PROGRESS_PATCH_ID = "agent-run-progress-projection-v1"

#: Canonical Temporal signal name for the projection.
AGENT_RUN_PROGRESS_SIGNAL_NAME = "agent_run_progress"

#: Retirement inventory classification for the compatibility handlers this
#: projection supersedes (MoonLadderStudios/MoonMind#3712, #3835). The legacy
#: ``child_state_changed`` / ``profile_assigned`` / ``managed_session_bound``
#: handlers stay registered for old histories; continuation, chat, and
#: cleanup support are never removed by the progress cutover.
AGENT_RUN_PROGRESS_RETIREMENT_INVENTORY: tuple[str, ...] = (
    "child_state_changed:legacy-product-progress:retained-for-old-histories",
    "profile_assigned:provider-capacity-compat:retained-#1089-owns-removal",
    "managed_session_bound:session-compat:retained",
    "continuation/chat/cleanup:control-plane:never-removed-by-progress-cutover",
)

#: Child-to-parent lifecycle signals classified under #3712/#3835 plus
#: legitimate control/replay messages. Architecture checks reject any other
#: new child-to-parent lifecycle signal without banning control or replay
#: traffic.
CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS: frozenset[str] = frozenset(
    {
        # Product progress (this issue).
        AGENT_RUN_PROGRESS_SIGNAL_NAME,
        # Retained legacy compatibility (#3712/#3835 inventory above).
        "child_state_changed",
        "profile_assigned",
        "managed_session_bound",
        # Legitimate control / replay messages (never banned).
        "completion_signal",
        "cancel_session",
        "finalize_session",
        "report_cooldown",
        "request_slot",
        "release_slot",
        "slot_assigned",
        "sync_profiles",
    }
)


def assert_classified_child_signal(signal_name: str) -> str:
    """Fail closed on unclassified new child-to-parent lifecycle signals.

    Legitimate control and replay messages in
    :data:`CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS` pass through.
    Anything else raises so a new lifecycle signal cannot slip in without
    retirement-inventory classification.
    """

    normalized = str(signal_name or "").strip()
    if normalized not in CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS:
        raise ValueError(
            f"unclassified child-to-parent lifecycle signal: {normalized!r}; "
            "classify it under #3712/#3835 before use"
        )
    return normalized


def use_agent_run_progress_projection(patched: bool) -> bool:
    """Return whether a history applies the new lifecycle projection.

    ``patched`` is the ``workflow.patched(AGENT_RUN_PROGRESS_PATCH_ID)``
    marker for the history. New (patched) histories apply only
    ``agent_run_progress``; old histories retain the legacy behavior.
    """

    return bool(patched)


# ---------------------------------------------------------------------------
# Bounded vocabularies
# ---------------------------------------------------------------------------

#: Bounded reason codes for the projection. ``none`` means the state alone
#: carries the product meaning; waiting/retry detail travels only as one of
#: the closed codes below, never as a free-form provider string and never by
#: parsing semicolon-delimited display strings into authority.
AgentRunProgressReasonCode = Literal[
    "none",
    "awaiting_provider_capacity",
    "awaiting_provider_validation",
    "awaiting_profile_maintenance",
    "provider_cooldown",
    "awaiting_host_capacity",
    "awaiting_host_launch_permit",
    "awaiting_execution_worker",
    "cleanup_pending",
    "launching",
    "running",
    "awaiting_callback",
    "awaiting_feedback",
    "awaiting_approval",
    "collecting_results",
    "terminal",
]

#: Bounded wait codes distinguishing *what* is waited on. ``none`` when the
#: projection is not a wait.
AgentRunProgressWaitCode = Literal[
    "none",
    "provider_capacity",
    "provider_validation",
    "profile_maintenance",
    "provider_cooldown",
    "host_capacity",
    "host_launch_permit",
    "execution_worker",
    "feedback",
    "approval",
    "callback",
    "evidence",
    "cleanup",
]

PROGRESS_REASON_CODES: tuple[str, ...] = (
    "none",
    "awaiting_provider_capacity",
    "awaiting_provider_validation",
    "awaiting_profile_maintenance",
    "provider_cooldown",
    "awaiting_host_capacity",
    "awaiting_host_launch_permit",
    "awaiting_execution_worker",
    "cleanup_pending",
    "launching",
    "running",
    "awaiting_callback",
    "awaiting_feedback",
    "awaiting_approval",
    "collecting_results",
    "terminal",
)

PROGRESS_WAIT_CODES: tuple[str, ...] = (
    "none",
    "provider_capacity",
    "provider_validation",
    "profile_maintenance",
    "provider_cooldown",
    "host_capacity",
    "host_launch_permit",
    "execution_worker",
    "feedback",
    "approval",
    "callback",
    "evidence",
    "cleanup",
)

#: Legacy ``child_state_changed`` states mapped to one canonical
#: (state, reason, wait) triple through a single table. There are no
#: provider or harness branches: every realizer maps through this table.
LEGACY_STATE_TO_PROGRESS: dict[str, tuple[str, str, str]] = {
    "queued": ("queued", "none", "none"),
    "awaiting_slot": ("awaiting_slot", "awaiting_provider_capacity", "provider_capacity"),
    "launching": ("launching", "launching", "none"),
    "running": ("running", "running", "none"),
    "awaiting_callback": ("awaiting_callback", "awaiting_callback", "callback"),
    "awaiting_feedback": ("awaiting_feedback", "awaiting_feedback", "feedback"),
    "awaiting_approval": ("awaiting_approval", "awaiting_approval", "approval"),
    "intervention_requested": ("intervention_requested", "awaiting_feedback", "feedback"),
    "collecting_results": ("collecting_results", "collecting_results", "evidence"),
    "completed": ("completed", "terminal", "none"),
    "failed": ("failed", "terminal", "none"),
    "canceled": ("canceled", "terminal", "none"),
    "cancelled": ("canceled", "terminal", "none"),
    "timed_out": ("timed_out", "terminal", "none"),
}

#: Forward progress ranks for domain regression checks. A projection may
#: stay at its rank (repeated run/feedback/evidence phases are legitimate)
#: or move forward; only an actual domain-invalid regression is prohibited.
#: Terminal ranks seal: progress can never move out of terminal.
PROGRESS_STATE_RANKS: dict[str, int] = {
    "queued": 0,
    "awaiting_slot": 1,
    "launching": 2,
    "running": 3,
    "awaiting_callback": 4,
    "awaiting_feedback": 4,
    "awaiting_approval": 4,
    "intervention_requested": 4,
    "collecting_results": 5,
    "completed": 6,
    "failed": 6,
    "canceled": 6,
    "timed_out": 6,
}

TERMINAL_PROGRESS_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "timed_out"}
)

#: Owner-allowed backward edges the rank table alone would reject
#: (MoonLadderStudios/MoonMind#1088 R6). Real ``MoonMindAgentRun`` producer
#: sequences return from a wait to launching/running when the owner answers
#: the wait (feedback/approval/callback answered, readiness restored,
#: intervention resolved), and return from launching to awaiting_slot when
#: capacity is requeued under the same owner. Every other backward move
#: stays a domain-invalid regression. Terminal states never appear here:
#: the terminal seal above owns them, so a higher revision still cannot
#: reopen a terminal attempt or overwrite a replacement child.
PROGRESS_LEGITIMATE_RESUME_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("awaiting_callback", "launching"),
        ("awaiting_callback", "running"),
        ("awaiting_feedback", "launching"),
        ("awaiting_feedback", "running"),
        ("awaiting_approval", "launching"),
        ("awaiting_approval", "running"),
        ("intervention_requested", "launching"),
        ("intervention_requested", "running"),
        ("launching", "awaiting_slot"),
    }
)


def coerce_legacy_progress_triple(new_state: str) -> tuple[str, str, str]:
    """Map a legacy child state to one canonical (state, reason, wait) triple.

    The mapping is total over the legacy vocabulary and closed: unknown
    states raise instead of inventing a code, and free-form reason text is
    never parsed for authority (it becomes a redacted summary only).
    """

    normalized = str(new_state or "").strip().lower()
    triple = LEGACY_STATE_TO_PROGRESS.get(normalized)
    if triple is None:
        raise ValueError(f"unsupported progress state: {new_state!r}")
    return triple


# ---------------------------------------------------------------------------
# Redaction and size limits
# ---------------------------------------------------------------------------

#: Maximum redacted summary length: compact by construction.
MAX_PROGRESS_SUMMARY_CHARS = 280

#: Maximum opaque diagnostic artifact reference length.
MAX_PROGRESS_DIAGNOSTIC_REF_CHARS = 1024

#: Fragments that must never appear in a recorded summary. Payloads carrying
#: them are rejected (strict): raw events/logs, provider session/turn IDs,
#: host/Docker/workspace locators, profile leases, credential generations,
#: credential handles, and chat-binding authority travel nowhere in progress.
_FORBIDDEN_SUMMARY_FRAGMENTS: tuple[str, ...] = (
    "session_id",
    "session-id",
    "turn_id",
    "turn-id",
    "docker://",
    "host:",
    "workspace:",
    "profile lease",
    "credential_generation",
    "credential_handle",
    "chat_binding",
    "chat-binding",
    "api_key",
    "apikey",
    "private_key",
    "authorization:",
)

_SENSITIVE_SUMMARY_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|private[_-]?key)\s*[:=]\s*\S+"
)

_DIAGNOSTIC_REF_PATTERN = re.compile(r"^(artifact://|art:|ref:)[A-Za-z0-9._\-/:]+$")


def redact_progress_summary(summary: str | None) -> str | None:
    """Redact and bound a candidate summary for the projection payload.

    Secret assignments are replaced with ``[REDACTED]``; the result is
    truncated to :data:`MAX_PROGRESS_SUMMARY_CHARS`. Fragments that would
    smuggle provider/host/credential/chat authority into progress raise
    instead of being recorded.
    """

    if summary is None:
        return None
    text = str(summary).strip()
    if not text:
        return None
    lowered = text.lower()
    for fragment in _FORBIDDEN_SUMMARY_FRAGMENTS:
        if fragment in lowered:
            raise ValueError(
                "progress summary must not carry provider/host/credential "
                f"authority (found {fragment!r})"
            )
    redacted = _SENSITIVE_SUMMARY_PATTERN.sub(r"\1=[REDACTED]", text)
    if len(redacted) > MAX_PROGRESS_SUMMARY_CHARS:
        redacted = redacted[: MAX_PROGRESS_SUMMARY_CHARS - 3] + "..."
    return redacted


def validate_diagnostic_artifact_ref(value: str | None) -> str | None:
    """Validate the optional diagnostic artifact reference shape.

    The ref must be opaque (never a filesystem path) and compact. Shape
    validation here does not grant access: the read boundary must still
    validate scope, digest, and access via
    :func:`validate_diagnostic_artifact_access` — possession of the ref is
    not authorization.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_PROGRESS_DIAGNOSTIC_REF_CHARS:
        raise ValueError("diagnosticArtifactRef exceeds the compact size limit")
    if text.startswith(("/", "./", "../", "~")):
        raise ValueError("diagnosticArtifactRef must be an opaque artifact reference")
    if not _DIAGNOSTIC_REF_PATTERN.match(text):
        raise ValueError(
            "diagnosticArtifactRef must be an opaque artifact reference "
            "(artifact://, art:, or ref:)"
        )
    return text


def validate_diagnostic_artifact_access(
    ref: str,
    *,
    scope: str,
    expected_digest: str | None,
    accessible_refs: Mapping[str, str],
) -> str:
    """Authorize reading a diagnostic artifact at the read boundary.

    ``accessible_refs`` maps refs the caller may read to their recorded
    digests within ``scope``. A ref that is missing, out of scope, or whose
    digest disagrees is rejected. Possession of the ref alone never grants
    access.
    """

    text = str(ref or "").strip()
    if not text:
        raise ValueError("diagnostic artifact ref is required")
    normalized_scope = str(scope or "").strip()
    if not normalized_scope:
        raise ValueError("diagnostic artifact scope is required")
    recorded_digest = accessible_refs.get(text)
    if recorded_digest is None:
        raise ValueError("diagnostic artifact ref is not accessible in scope")
    if expected_digest is not None and recorded_digest != expected_digest:
        raise ValueError("diagnostic artifact digest mismatch")
    return text


# ---------------------------------------------------------------------------
# Strict versioned schema
# ---------------------------------------------------------------------------


class AgentRunProgressProjection(BaseModel):
    """One immutable product-progress observation for a parent workflow.

    Compact by construction: ``extra="forbid"`` excludes arbitrary
    metadata, raw events/logs, provider session/turn IDs, host/Docker/
    workspace locators, profile leases, credential generations, credential
    handles, and chat-binding authority. Safe display labels are resolved
    separately from the recorded plan; only the validated diagnostic ref
    crosses the boundary.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["agent-run-progress/v1"] = Field(
        AGENT_RUN_PROGRESS_SCHEMA_VERSION, alias="schemaVersion"
    )
    #: Workflow-owned AgentRun identity (the child workflow ID). This is the
    #: parent-owned fence, never a provider ``AgentRunStatus.runId`` copy.
    agent_run_workflow_id: str = Field(..., alias="agentRunWorkflowId", min_length=1)
    #: Emitting Temporal run within the current source generation. Optional
    #: only while the run identity is still being established (bounded
    #: pending observation for the known child).
    agent_run_run_id: str | None = Field(None, alias="agentRunRunId")
    #: Parent UserWorkflow that owns the Step projection.
    source_workflow_id: str = Field(..., alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(..., alias="sourceRunId", min_length=1)
    #: Step Execution / attempt identity the progress belongs to.
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    attempt_index: int = Field(0, alias="attemptIndex", ge=0)
    #: Logical source generation established by the parent's child lifecycle
    #: (child retries, replacement attempts, Continue-As-New). Revisions
    #: order within a generation, never by timestamp or UUID.
    source_generation: str = Field(..., alias="sourceGeneration", min_length=1)
    #: Monotonic projection revision within the source generation (1-based).
    projection_revision: int = Field(..., alias="projectionRevision", ge=1)
    #: Canonical product state. Reuses ``AgentRunState``, not the
    #: ``AgentRunStatus`` response envelope or another enum.
    state: AgentRunState = Field(...)
    reason_code: AgentRunProgressReasonCode = Field("none", alias="reasonCode")
    wait_code: AgentRunProgressWaitCode = Field("none", alias="waitCode")
    summary: str | None = Field(None)
    attention_required: bool = Field(False, alias="attentionRequired")
    diagnostic_artifact_ref: str | None = Field(None, alias="diagnosticArtifactRef")

    @field_validator(
        "agent_run_workflow_id",
        "source_workflow_id",
        "source_run_id",
        "step_execution_id",
        "source_generation",
        mode="after",
    )
    @classmethod
    def _non_blank_identity(cls, value: str) -> str:
        return require_non_blank(value, field_name="agentRunProgress identity")

    @field_validator("agent_run_run_id", mode="after")
    @classmethod
    def _optional_run_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_blank(value, field_name="agentRunProgress.agentRunRunId")

    @field_validator("summary", mode="after")
    @classmethod
    def _redacted_summary(cls, value: str | None) -> str | None:
        # Strict compact bound enforced here (not as a field max_length so
        # overlong input is truncated, never a history-poisoning error).
        return redact_progress_summary(value)

    @field_validator("diagnostic_artifact_ref", mode="after")
    @classmethod
    def _validated_diagnostic_ref(cls, value: str | None) -> str | None:
        return validate_diagnostic_artifact_ref(value)

    @model_validator(mode="after")
    def _validate_state_reason_agreement(self) -> "AgentRunProgressProjection":
        terminal = self.state in TERMINAL_PROGRESS_STATES
        if terminal and self.reason_code != "terminal":
            raise ValueError("terminal progress states require reasonCode terminal")
        if not terminal and self.reason_code == "terminal":
            raise ValueError("non-terminal progress states cannot use reasonCode terminal")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """Return the immutable canonical mapping bound to this revision."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def projection_digest(payload: Mapping[str, Any]) -> str:
    """Return the immutable digest bound to one logical revision payload."""

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_progress_projection(
    *,
    agent_run_workflow_id: str,
    source_workflow_id: str,
    source_run_id: str,
    step_execution_id: str,
    source_generation: str,
    projection_revision: int,
    state: str,
    reason_code: str = "none",
    wait_code: str = "none",
    summary: str | None = None,
    attention_required: bool = False,
    diagnostic_artifact_ref: str | None = None,
    agent_run_run_id: str | None = None,
    attempt_index: int = 0,
) -> AgentRunProgressProjection:
    """Build one validated immutable projection payload for a revision."""

    return AgentRunProgressProjection.model_validate(
        {
            "schemaVersion": AGENT_RUN_PROGRESS_SCHEMA_VERSION,
            "agentRunWorkflowId": agent_run_workflow_id,
            "agentRunRunId": agent_run_run_id,
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
            "stepExecutionId": step_execution_id,
            "attemptIndex": attempt_index,
            "sourceGeneration": source_generation,
            "projectionRevision": projection_revision,
            "state": state,
            "reasonCode": reason_code,
            "waitCode": wait_code,
            "summary": summary,
            "attentionRequired": attention_required,
            "diagnosticArtifactRef": diagnostic_artifact_ref,
        }
    )


# ---------------------------------------------------------------------------
# Deterministic emitter (child side)
# ---------------------------------------------------------------------------


def _progress_signature(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the meaning signature used for delivery coalescing."""

    return (
        payload.get("state"),
        payload.get("reasonCode"),
        payload.get("waitCode"),
        payload.get("attentionRequired"),
        payload.get("summary"),
        payload.get("diagnosticArtifactRef"),
    )


@dataclass
class AgentRunProgressEmitter:
    """Deterministic child-side emitter for one source generation.

    Emits only meaningful state/reason changes: repeated observations with
    an unchanged signature are coalesced so heartbeats, token-by-token
    output, and provider events never grow parent history. A payload that
    was built but not yet delivered stays pending and is retried at the
    same revision. Parent closure or temporary delivery failure never
    cancels agent work and never erases terminal evidence: the emitter
    only moves the pending pointer on positive delivery.
    """

    agent_run_workflow_id: str
    source_workflow_id: str
    source_run_id: str
    step_execution_id: str
    source_generation: str
    attempt_index: int = 0
    agent_run_run_id: str | None = None
    _next_revision: int = 1
    _last_signature: tuple[Any, ...] | None = None
    _pending: dict[str, Any] | None = None
    _pending_digest: str | None = None

    def build(
        self,
        *,
        state: str,
        reason_code: str = "none",
        wait_code: str = "none",
        summary: str | None = None,
        attention_required: bool = False,
        diagnostic_artifact_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build (but do not yet acknowledge) the next revision payload."""

        projection = build_progress_projection(
            agent_run_workflow_id=self.agent_run_workflow_id,
            source_workflow_id=self.source_workflow_id,
            source_run_id=self.source_run_id,
            step_execution_id=self.step_execution_id,
            source_generation=self.source_generation,
            projection_revision=self._next_revision,
            state=state,
            reason_code=reason_code,
            wait_code=wait_code,
            summary=summary,
            attention_required=attention_required,
            diagnostic_artifact_ref=diagnostic_artifact_ref,
            agent_run_run_id=self.agent_run_run_id,
            attempt_index=self.attempt_index,
        )
        payload = projection.canonical_dict()
        self._pending = payload
        self._pending_digest = projection_digest(payload)
        return payload

    def should_emit(self, payload: Mapping[str, Any] | None = None) -> bool:
        """Return whether the candidate carries a meaningful change."""

        candidate = payload if payload is not None else self._pending
        if candidate is None:
            return False
        return _progress_signature(candidate) != self._last_signature

    def mark_delivered(self) -> dict[str, Any] | None:
        """Advance past the pending revision after positive delivery."""

        if self._pending is None:
            return None
        self._last_signature = _progress_signature(self._pending)
        self._next_revision = int(self._pending.get("projectionRevision", 0)) + 1
        delivered, self._pending = self._pending, None
        self._pending_digest = None
        return delivered

    def retry_pending(self) -> dict[str, Any] | None:
        """Return the same pending revision for a bounded retry."""

        return dict(self._pending) if self._pending is not None else None

    @property
    def pending_revision(self) -> int | None:
        """Return the pending revision, if any."""

        if self._pending is None:
            return None
        revision = self._pending.get("projectionRevision")
        return int(revision) if revision is not None else None

    def next_revision(self) -> int:
        """Return the revision the next built payload will carry."""

        return self._next_revision


# ---------------------------------------------------------------------------
# Parent reducer (UserWorkflow side)
# ---------------------------------------------------------------------------

#: Dispositions for one applied projection. Invalid input yields bounded
#: safe diagnostics (``invalid`` / ``wrong_identity`` / ``old_generation``)
#: rather than poisoning the workflow task.
ProgressApplyDisposition = Literal[
    "accepted",
    "duplicate",
    "conflict",
    "stale",
    "wrong_identity",
    "old_generation",
    "terminal_sealed",
    "invalid",
]


@dataclass
class ProgressApplyOutcome:
    """Bounded safe diagnostic for one applied projection."""

    disposition: ProgressApplyDisposition
    diagnostics: str
    accepted_state: dict[str, Any] | None = None


def child_source_generation(agent_run_workflow_id: str) -> str:
    """Return the logical source generation for one AgentRun child workflow.

    The generation is the workflow-owned AgentRun identity itself: stable
    across the workflow's own run retries and Continue-As-New, renewed on
    replacement attempts (new child workflow ID). Revisions order within
    the generation; distinct Temporal runs within the generation are
    fenced by run ID so delayed old-run messages cannot reopen state.
    """

    return require_non_blank(
        agent_run_workflow_id, field_name="agentRunWorkflowId"
    )


def new_progress_parent_state(
    *,
    expected_agent_run_workflow_id: str,
    expected_step_execution_id: str,
) -> dict[str, Any]:
    """Record the expected child/Step identity before awaiting start.

    This closes the child-start race: only the known child is observed,
    with a bounded pending observation while its run identity is still
    being established. Payload identity fields remain fences, never proof
    of sender permission — the authorized signal ingress and execution
    ownership checks stay responsible for sender authorization.
    """

    return {
        "expectedAgentRunWorkflowId": require_non_blank(
            expected_agent_run_workflow_id,
            field_name="expectedAgentRunWorkflowId",
        ),
        "expectedStepExecutionId": require_non_blank(
            expected_step_execution_id,
            field_name="expectedStepExecutionId",
        ),
        "acceptedSourceGeneration": None,
        "acceptedAgentRunRunId": None,
        "acceptedRevision": 0,
        "acceptedDigest": None,
        "acceptedState": None,
        "acceptedReasonCode": None,
        "acceptedWaitCode": None,
        "supersededAgentRunRunIds": [],
        "pendingSuccessorGeneration": None,
        "terminalSealed": False,
        "terminalStatus": None,
        "pendingObservation": None,
    }


def note_successor_generation(
    parent_state: dict[str, Any],
    *,
    new_generation: str,
) -> dict[str, Any]:
    """Establish a successor run/attempt identity from parent lifecycle.

    Called from the parent's child lifecycle (replacement attempt with a
    new child workflow ID, or canonical control evidence) *before* the
    successor's updates are accepted. The first message carrying the noted
    generation adopts it; messages carrying any other unaccepted
    generation are rejected as ``old_generation``. A successor is never
    adopted from an arbitrary new run ID on first message: generation
    adoption still requires the expected child/Step fence to match.
    """

    generation = require_non_blank(new_generation, field_name="sourceGeneration")
    if parent_state.get("acceptedSourceGeneration") == generation:
        return parent_state
    parent_state["pendingSuccessorGeneration"] = generation
    return parent_state


def seal_terminal_result(parent_state: dict[str, Any], *, status: str) -> dict[str, Any]:
    """Seal the product outcome with the validated terminal child result.

    A terminal child result takes precedence over late or disagreeing
    progress: after sealing, further projections are ``terminal_sealed``
    and the recorded outcome never moves.
    """

    parent_state["terminalSealed"] = True
    parent_state["terminalStatus"] = str(status or "").strip() or "completed"
    return parent_state


def apply_agent_run_progress(
    parent_state: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    terminal_sealed: bool | None = None,
) -> ProgressApplyOutcome:
    """Apply one projection payload through the single parent reducer.

    Never raises on invalid input: validation failures return bounded safe
    diagnostics so a malformed signal cannot poison the workflow task with
    repeated unhandled failures.
    """

    sealed = (
        terminal_sealed
        if terminal_sealed is not None
        else bool(parent_state.get("terminalSealed"))
    )
    try:
        projection = AgentRunProgressProjection.model_validate(dict(payload))
    except Exception as exc:
        return ProgressApplyOutcome(
            disposition="invalid",
            diagnostics=f"invalid agent_run_progress payload: {exc}",
        )

    if sealed:
        return ProgressApplyOutcome(
            disposition="terminal_sealed",
            diagnostics="terminal child result already seals the product outcome",
        )

    expected_child = str(parent_state.get("expectedAgentRunWorkflowId") or "")
    expected_step = str(parent_state.get("expectedStepExecutionId") or "")
    if (
        not expected_child
        or projection.agent_run_workflow_id != expected_child
        or projection.step_execution_id != expected_step
    ):
        return ProgressApplyOutcome(
            disposition="wrong_identity",
            diagnostics=(
                "progress identity does not match the expected child/Step fence"
            ),
        )

    accepted_generation = parent_state.get("acceptedSourceGeneration")
    if accepted_generation is None:
        # First contact from the expected child binds the generation (and
        # the emitting run, when known). The expected child/Step fence
        # above already rejected strangers, so no arbitrary run ID is
        # adopted here: only the awaited child establishes lineage.
        parent_state["acceptedSourceGeneration"] = projection.source_generation
        if projection.agent_run_run_id is not None:
            parent_state["acceptedAgentRunRunId"] = projection.agent_run_run_id
    elif projection.source_generation != accepted_generation:
        if (
            parent_state.get("pendingSuccessorGeneration") is not None
            and projection.source_generation
            == parent_state.get("pendingSuccessorGeneration")
        ):
            parent_state["acceptedSourceGeneration"] = projection.source_generation
            parent_state["acceptedAgentRunRunId"] = projection.agent_run_run_id
            parent_state["acceptedRevision"] = 0
            parent_state["acceptedDigest"] = None
            parent_state["acceptedState"] = None
            parent_state["acceptedReasonCode"] = None
            parent_state["acceptedWaitCode"] = None
            parent_state["pendingSuccessorGeneration"] = None
        else:
            return ProgressApplyOutcome(
                disposition="old_generation",
                diagnostics=(
                    "progress from a superseded source generation is ignored; "
                    "old-run messages cannot reopen a replacement or a "
                    "terminal result"
                ),
            )
    elif (
        parent_state.get("pendingSuccessorGeneration") is not None
        and parent_state.get("pendingSuccessorGeneration") != accepted_generation
    ):
        # A noted successor immediately supersedes the previously accepted
        # generation: old-generation messages cannot reopen a replacement
        # while the successor is pending. The first message carrying the
        # noted generation adopts it (handled above); anything else from
        # the old generation is ignored as display-only because terminal
        # authority already comes from AgentRunResult, not progress.
        return ProgressApplyOutcome(
            disposition="old_generation",
            diagnostics=(
                "a successor generation is pending; progress from the "
                "superseded source generation is ignored"
            ),
        )

    # Run fence within a generation: distinct Temporal runs (child retry,
    # replacement attempt, Continue-As-New) carry distinct run IDs. A new
    # run adopts the baseline and supersedes the old one; messages from a
    # superseded run are then always rejected so delayed old-run messages
    # cannot reopen state.
    incoming_run = projection.agent_run_run_id
    accepted_run = parent_state.get("acceptedAgentRunRunId")
    if incoming_run is not None and accepted_run is not None and incoming_run != accepted_run:
        superseded = parent_state.get("supersededAgentRunRunIds") or []
        if incoming_run in superseded:
            return ProgressApplyOutcome(
                disposition="old_generation",
                diagnostics="delayed message from a superseded run is ignored",
            )
        superseded = list(superseded) + [accepted_run]
        parent_state["supersededAgentRunRunIds"] = superseded[-8:]
        parent_state["acceptedAgentRunRunId"] = incoming_run
        parent_state["acceptedRevision"] = 0
        parent_state["acceptedDigest"] = None
        parent_state["acceptedState"] = None
        parent_state["acceptedReasonCode"] = None
        parent_state["acceptedWaitCode"] = None
    elif incoming_run is not None and accepted_run is None:
        parent_state["acceptedAgentRunRunId"] = incoming_run

    accepted_revision = int(parent_state.get("acceptedRevision") or 0)
    if projection.projection_revision < accepted_revision:
        return ProgressApplyOutcome(
            disposition="stale",
            diagnostics="older revision is ignored",
        )
    if projection.projection_revision == accepted_revision:
        digest = projection_digest(projection.canonical_dict())
        if digest == parent_state.get("acceptedDigest"):
            return ProgressApplyOutcome(
                disposition="duplicate",
                diagnostics="same revision/same payload is a duplicate",
            )
        return ProgressApplyOutcome(
            disposition="conflict",
            diagnostics=(
                "same revision/different payload is a conflict and cannot "
                "overwrite accepted state"
            ),
        )

    # Domain regression gate: legitimate repeated run/feedback/evidence
    # phases share a rank and always pass; only an actual domain-invalid
    # regression is prohibited. Terminal progress seals the projection.
    previous_state = parent_state.get("acceptedState")
    if previous_state is not None:
        previous_rank = PROGRESS_STATE_RANKS.get(str(previous_state), -1)
        next_rank = PROGRESS_STATE_RANKS.get(projection.state, -1)
        if previous_state in TERMINAL_PROGRESS_STATES:
            return ProgressApplyOutcome(
                disposition="stale",
                diagnostics="accepted terminal progress cannot regress",
            )
        if next_rank < previous_rank and (
            str(previous_state),
            str(projection.state),
        ) not in PROGRESS_LEGITIMATE_RESUME_EDGES:
            return ProgressApplyOutcome(
                disposition="stale",
                diagnostics=(
                    f"domain-invalid regression {previous_state} -> "
                    f"{projection.state} is ignored"
                ),
            )

    canonical = projection.canonical_dict()
    parent_state["acceptedRevision"] = projection.projection_revision
    parent_state["acceptedDigest"] = projection_digest(canonical)
    parent_state["acceptedState"] = projection.state
    parent_state["acceptedReasonCode"] = projection.reason_code
    parent_state["acceptedWaitCode"] = projection.wait_code
    parent_state["pendingObservation"] = None
    if projection.state in TERMINAL_PROGRESS_STATES:
        # Terminal *progress* seals the projection only; the authoritative
        # product outcome still comes from the validated AgentRunResult via
        # seal_terminal_result. Late or disagreeing progress after that is
        # terminal_sealed.
        parent_state["terminalSealed"] = True
        parent_state["terminalStatus"] = projection.state
    return ProgressApplyOutcome(
        disposition="accepted",
        diagnostics="progress accepted",
        accepted_state=dict(canonical),
    )


# ---------------------------------------------------------------------------
# Step/Workflow mapping: one reducer
# ---------------------------------------------------------------------------


@dataclass
class StepProgressView:
    """UserWorkflow Step/Workflow waiting, summary, and attention fields."""

    waiting_reason: str | None
    summary: str | None
    attention_required: bool


#: Waiting reasons the reducer may emit. Compatible with legitimate
#: repeated run/feedback/evidence phases; coordinated with #1130's timeline
#: and #3880/#3881's provider/host waits without parsing display strings.
STEP_WAITING_REASONS: tuple[str, ...] = (
    "provider_capacity",
    "provider_validation",
    "profile_maintenance",
    "provider_cooldown",
    "host_capacity",
    "host_launch_permit",
    "execution_worker",
    "feedback",
    "approval",
    "callback",
    "evidence",
    "cleanup",
)

_WAIT_CODE_TO_WAITING_REASON: dict[str, str] = {
    "provider_capacity": "provider_capacity",
    "provider_validation": "provider_validation",
    "profile_maintenance": "profile_maintenance",
    "provider_cooldown": "provider_cooldown",
    "host_capacity": "host_capacity",
    "host_launch_permit": "host_launch_permit",
    "execution_worker": "execution_worker",
    "feedback": "feedback",
    "approval": "approval",
    "callback": "callback",
    "evidence": "evidence",
    "cleanup": "cleanup",
}

_STATE_SUMMARIES: dict[str, str] = {
    "queued": "Agent run queued.",
    "awaiting_slot": "Waiting for provider capacity.",
    "launching": "Launching agent...",
    "running": "Agent is running.",
    "awaiting_callback": "Waiting for provider callback.",
    "awaiting_feedback": "Waiting for feedback.",
    "awaiting_approval": "Waiting for approval.",
    "intervention_requested": "Operator intervention requested.",
    "collecting_results": "Collecting results.",
    "completed": "Agent run completed.",
    "failed": "Agent run failed.",
    "canceled": "Agent run canceled.",
    "timed_out": "Agent run timed out.",
}


def progress_step_logical_id_for_child(
    rows: Any,
    child_workflow_id: str,
) -> str | None:
    """Return the logical Step id owning one AgentRun child workflow.

    MoonLadderStudios/MoonMind#1088 R4: accepted progress is reflected in
    the owning per-row Step ledger entry through the existing
    awaiting-external row path. Ownership is the recorded
    ``refs.childWorkflowId`` fence written when the parent launched the
    child — never the untrusted payload identity fields, which the
    reducer already fenced. Pure display lookup: unknown children yield
    ``None`` so the caller keeps the workflow-level update only.
    """

    target = str(child_workflow_id or "").strip()
    if not target or not isinstance(rows, (list, tuple)):
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        refs = row.get("refs")
        if not isinstance(refs, Mapping):
            continue
        if str(refs.get("childWorkflowId") or "").strip() != target:
            continue
        logical_step_id = str(row.get("logicalStepId") or "").strip()
        return logical_step_id or None
    return None


def reduce_progress_to_step(accepted: Mapping[str, Any]) -> StepProgressView:
    """Map accepted progress into Step/Workflow fields through one reducer.

    The single mapping point for every realizer: no provider or harness
    branches exist in UserWorkflow. Detailed events and warnings stay in
    the existing timeline; a completed turn never completes a Step here —
    only the sealed terminal result does that.
    """

    state = str(accepted.get("state") or "")
    wait_code = str(accepted.get("waitCode") or "none")
    summary = accepted.get("summary") or _STATE_SUMMARIES.get(state)
    attention = bool(accepted.get("attentionRequired", False))
    if state in TERMINAL_PROGRESS_STATES:
        return StepProgressView(
            waiting_reason=None, summary=summary, attention_required=attention
        )
    waiting = _WAIT_CODE_TO_WAITING_REASON.get(wait_code)
    return StepProgressView(
        waiting_reason=waiting, summary=summary, attention_required=attention
    )


# ---------------------------------------------------------------------------
# Delivery repair through the existing Activity boundary
# ---------------------------------------------------------------------------

#: Authorized latest-state read used for projection repair. This reuses the
#: existing Activity boundary (managed/agent status read); a repair timeout
#: is display-only and never becomes canonical execution failure. No second
#: event store or background service exists for progress.
PROGRESS_REPAIR_ACTIVITY = "agent_runtime.session_status"

#: Names that must never appear as progress infrastructure. Architecture
#: tests assert the projection introduces no second event store, background
#: service, or lease system.
FORBIDDEN_PROGRESS_INFRASTRUCTURE: tuple[str, ...] = (
    "ProgressEventStore",
    "progress_event_store",
    "ProgressBackgroundService",
    "progress_background_service",
    "ProgressLeaseManager",
    "progress_lease_manager",
)


def build_progress_repair_read(
    *,
    agent_run_workflow_id: str,
    step_execution_id: str,
) -> dict[str, Any]:
    """Return the authorized latest-state repair read descriptor.

    Repair reuses the existing Activity boundary named by
    :data:`PROGRESS_REPAIR_ACTIVITY`. The read refreshes display state
    only: it cannot duplicate side effects, leak resources, or convert a
    display-repair timeout into execution failure.
    """

    return {
        "activity": PROGRESS_REPAIR_ACTIVITY,
        "agentRunWorkflowId": require_non_blank(
            agent_run_workflow_id, field_name="agentRunWorkflowId"
        ),
        "stepExecutionId": require_non_blank(
            step_execution_id, field_name="stepExecutionId"
        ),
        "purpose": "agent-run-progress-display-repair",
    }


@dataclass
class RolloverSnapshot:
    """Accepted source/revision/digest carried through parent rollover."""

    source_generation: str | None = None
    agent_run_run_id: str | None = None
    revision: int = 0
    digest: str | None = None
    superseded_run_ids: list[str] = field(default_factory=list)

    @classmethod
    def capture(cls, parent_state: Mapping[str, Any]) -> "RolloverSnapshot":
        """Persist the accepted lineage across Continue-As-New."""

        superseded = parent_state.get("supersededAgentRunRunIds") or []
        return cls(
            source_generation=parent_state.get("acceptedSourceGeneration"),
            agent_run_run_id=parent_state.get("acceptedAgentRunRunId"),
            revision=int(parent_state.get("acceptedRevision") or 0),
            digest=parent_state.get("acceptedDigest"),
            superseded_run_ids=list(superseded)[-8:],
        )

    def restore(self, parent_state: dict[str, Any]) -> dict[str, Any]:
        """Restore accepted lineage so old-run messages cannot reopen state."""

        if self.source_generation is not None:
            parent_state["acceptedSourceGeneration"] = self.source_generation
        parent_state["acceptedAgentRunRunId"] = self.agent_run_run_id
        parent_state["acceptedRevision"] = self.revision
        parent_state["acceptedDigest"] = self.digest
        parent_state["supersededAgentRunRunIds"] = list(self.superseded_run_ids)
        return parent_state


__all__ = [
    "AGENT_RUN_PROGRESS_PATCH_ID",
    "AGENT_RUN_PROGRESS_RETIREMENT_INVENTORY",
    "AGENT_RUN_PROGRESS_SCHEMA_VERSION",
    "AGENT_RUN_PROGRESS_SIGNAL_NAME",
    "CLASSIFIED_CHILD_TO_PARENT_LIFECYCLE_SIGNALS",
    "FORBIDDEN_PROGRESS_INFRASTRUCTURE",
    "LEGACY_STATE_TO_PROGRESS",
    "MAX_PROGRESS_DIAGNOSTIC_REF_CHARS",
    "MAX_PROGRESS_SUMMARY_CHARS",
    "PROGRESS_LEGITIMATE_RESUME_EDGES",
    "PROGRESS_REASON_CODES",
    "PROGRESS_REPAIR_ACTIVITY",
    "PROGRESS_STATE_RANKS",
    "PROGRESS_WAIT_CODES",
    "STEP_WAITING_REASONS",
    "TERMINAL_PROGRESS_STATES",
    "AgentRunProgressEmitter",
    "AgentRunProgressProjection",
    "ProgressApplyOutcome",
    "RolloverSnapshot",
    "StepProgressView",
    "assert_classified_child_signal",
    "apply_agent_run_progress",
    "build_progress_projection",
    "build_progress_repair_read",
    "child_source_generation",
    "coerce_legacy_progress_triple",
    "new_progress_parent_state",
    "note_successor_generation",
    "progress_step_logical_id_for_child",
    "projection_digest",
    "redact_progress_summary",
    "reduce_progress_to_step",
    "seal_terminal_result",
    "use_agent_run_progress_projection",
    "validate_diagnostic_artifact_access",
    "validate_diagnostic_artifact_ref",
]
