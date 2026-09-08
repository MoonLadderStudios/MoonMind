"""Durable per-run evidence for worker pause/resume fan-out."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Dispositions that satisfy the scoped pause/resume operation without further
# action. `already_terminal` covers runs that closed before confirmation was
# observed; it must never be presented as verified physical host cleanup.
SATISFIED_PAUSE_STATES = frozenset({"safe_point", "already_terminal"})
SATISFIED_RESUME_STATES = frozenset({"resumed", "already_terminal"})

# Terminal per-target dispositions retained across observers. Once a target
# reaches one of these states, later observations must not overwrite it.
CONFIRMED_TARGET_STATES = frozenset(
    {"safe_point", "resumed", "failed", "already_terminal", "unsupported", "superseded"}
)

# Batch-level terminal outcomes. Snapshot reads must not trigger Temporal work
# for these batches; progress requires idempotent resubmission of a
# non-terminal request.
TERMINAL_BATCH_STATUSES = frozenset({"succeeded", "failed", "empty"})


class WorkflowControlTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(alias="workflowId")
    run_id: str = Field(alias="runId")
    update_id: str = Field(alias="updateId")
    state: Literal[
        "requested",
        "accepted",
        "pending",
        "safe_point",
        "resumed",
        "failed",
        "unknown",
        "already_terminal",
        "unsupported",
        "superseded",
    ] = "requested"
    reason: str | None = None


class WorkflowControlBatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(alias="requestId")
    action: Literal["Pause", "Resume"]
    targets: list[WorkflowControlTarget] = Field(default_factory=list)
    enumerated: bool = False
    enumeration_error: str | None = Field(default=None, alias="enumerationError")
    generation: int = Field(default=0, ge=0)
    # Bounded paged enumeration checkpoint. `enumeration_cursor` records the
    # checkpointed target count, so a retry resumes after safe progress instead
    # of repeating the full scan. `selection_policy` records the Visibility
    # query/page policy; the observed set is a paged selection, never an atomic
    # system snapshot.
    enumeration_cursor: str | None = Field(default=None, alias="enumerationCursor")
    enumeration_page_size: int = Field(default=100, ge=1, le=1000, alias="enumerationPageSize")
    selection_policy: str | None = Field(default=None, alias="selectionPolicy")

    @property
    def satisfied_states(self) -> frozenset[str]:
        return SATISFIED_PAUSE_STATES if self.action == "Pause" else SATISFIED_RESUME_STATES

    @property
    def is_complete(self) -> bool:
        """Whether enumeration finished without a blocking error."""
        return self.enumerated and not self.enumeration_error

    @property
    def status(self) -> str:
        if not self.enumerated:
            return "unknown" if self.enumeration_error else "requested"
        if self.enumeration_error:
            # Incomplete enumeration cannot produce a fully confirmed result.
            states = [target.state for target in self.targets]
            if not states:
                return "unknown"
            if any(state in {"failed", "unknown", "unsupported", "superseded"} for state in states):
                return "unknown"
            return "pending"
        if not self.targets:
            # An enumerated empty target list means no eligible runs were
            # found, not that every worker/host is quiescent.
            return "empty"
        satisfied = self.satisfied_states
        states = [target.state for target in self.targets]
        if all(state in satisfied for state in states):
            return "succeeded"
        if all(state in {"failed", "unsupported", "superseded"} for state in states):
            return "failed"
        if any(state in {"failed", "unknown", "unsupported", "superseded"} for state in states):
            return (
                "partial"
                if any(state not in {"failed", "unknown", "unsupported", "superseded"} for state in states)
                else "unknown"
            )
        return "pending"
