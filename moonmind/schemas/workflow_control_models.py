"""Durable per-run evidence for worker pause/resume fan-out."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowControlTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(alias="workflowId")
    run_id: str = Field(alias="runId")
    update_id: str = Field(alias="updateId")
    state: Literal[
        "requested", "accepted", "pending", "safe_point", "resumed", "failed", "unknown"
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

    @property
    def status(self) -> str:
        if not self.enumerated:
            return "unknown" if self.enumeration_error else "requested"
        success = "safe_point" if self.action == "Pause" else "resumed"
        states = [target.state for target in self.targets]
        if all(state == success for state in states):
            return "succeeded"
        if all(state == "failed" for state in states):
            return "failed"
        if any(state in {"failed", "unknown"} for state in states):
            return (
                "partial"
                if any(state not in {"failed", "unknown"} for state in states)
                else "unknown"
            )
        return "pending"
