"""Durable per-run evidence for worker pause/resume fan-out."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


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

    #: Terminal observations a later observer must never overwrite.
    TERMINAL_STATES: ClassVar[frozenset[str]] = frozenset(
        {
            "safe_point",
            "resumed",
            "failed",
            "already_terminal",
            "unsupported",
            "superseded",
        }
    )

    #: Observations that still need operator attention (never satisfy scope).
    ATTENTION_STATES: ClassVar[frozenset[str]] = frozenset(
        {"failed", "unknown", "unsupported", "superseded"}
    )


class WorkflowControlBatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(alias="requestId")
    action: Literal["Pause", "Resume"]
    targets: list[WorkflowControlTarget] = Field(default_factory=list)
    enumerated: bool = False
    enumeration_error: str | None = Field(default=None, alias="enumerationError")
    generation: int = Field(default=0, ge=0)
    #: Informational resume marker: last workflow id observed before a
    #: truncated/failed enumeration pass. Retries re-scan Visibility and
    #: deduplicate against already-persisted targets; the marker only records
    #: how far the previous pass progressed.
    enumeration_cursor: str | None = Field(default=None, alias="enumerationCursor")
    #: Point-in-time selection policy for the Visibility scan (query, queues).
    #: The observed set is never claimed to be an atomic system snapshot.
    enumeration_policy: str | None = Field(default=None, alias="enumerationPolicy")

    @property
    def status(self) -> str:
        if not self.enumerated:
            return "unknown" if self.enumeration_error else "requested"
        if self.enumeration_error and not self.targets:
            return "unknown"
        if not self.targets:
            # No eligible runs were found. This is terminal for the request
            # but must never read as proof that every host/worker is quiet.
            return "empty"
        success = "safe_point" if self.action == "Pause" else "resumed"
        satisfied = {success, "already_terminal"}
        states = [target.state for target in self.targets]
        attention = WorkflowControlTarget.ATTENTION_STATES
        if all(state in satisfied for state in states):
            # A blocked/truncated enumeration may hide further targets, so an
            # incomplete scan can never report a fully confirmed result.
            return "partial" if self.enumeration_error else "succeeded"
        if all(state == "failed" for state in states):
            return "failed"
        if any(state in attention for state in states):
            return (
                "partial"
                if any(state not in attention for state in states)
                else "unknown"
            )
        return "pending"
