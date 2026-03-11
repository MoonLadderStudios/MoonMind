"""Standalone Pydantic schemas for queue-system-level metadata.

These models are kept in a separate module that has NO imports from
``api_service.db.models`` or ``moonmind.schemas.workflow_models`` so that they
can be safely imported from both sides of the codebase without triggering the
circular-import cycle:

    api_service.db.models
      → moonmind.workflows.agent_queue
      → moonmind.schemas.agent_queue_models
      → api_service.api.schemas          ← was circular here
      → moonmind.schemas.workflow_models
      → api_service.db.models            ← back to start
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from moonmind.workflows.agent_queue.service import QueueSystemMetadata


class QueueSystemMetadataModel(BaseModel):
    """Serialized worker pause metadata shared by claim + heartbeat responses."""

    model_config = ConfigDict(populate_by_name=True)

    workers_paused: bool = Field(..., alias="workersPaused")
    mode: Optional[Literal["drain", "quiesce"]] = Field(None, alias="mode")
    reason: Optional[str] = Field(None, alias="reason")
    version: int = Field(..., alias="version", ge=1)
    requested_by_user_id: Optional[uuid.UUID] = Field(None, alias="requestedByUserId")
    requested_at: Optional[datetime] = Field(None, alias="requestedAt")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")

    @staticmethod
    def from_service_metadata(
        metadata: "QueueSystemMetadata",
    ) -> "QueueSystemMetadataModel":
        mode_value: str | None
        if metadata.mode is None:
            mode_value = None
        elif getattr(metadata.mode, "value", None) is not None:
            mode_value = str(metadata.mode.value).strip() or None
        else:
            mode_value = str(metadata.mode).strip() or None

        return QueueSystemMetadataModel(
            workers_paused=metadata.workers_paused,
            mode=mode_value,
            reason=metadata.reason,
            version=metadata.version,
            requested_by_user_id=metadata.requested_by_user_id,
            requested_at=metadata.requested_at,
            updated_at=metadata.updated_at,
        )


__all__ = ["QueueSystemMetadataModel"]
