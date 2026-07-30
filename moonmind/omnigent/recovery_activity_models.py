"""Typed activity-input contracts for coordinator-backed checkpoint recovery.

These models are the compact, Temporal-serializable payloads for the two
production activities that invoke the Omnigent profile-bound coordinator's
``recover_from_checkpoint()`` and ``branch_from_checkpoint()`` methods. They
carry only durable refs and compact authority metadata (no credential data);
credential/session material is resolved inside the coordinator behind the
Activity boundary.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class OmnigentCheckpointRecoveryRequest(BaseModel):
    """Input for the coordinator live-reattach / cold-restore recovery path."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request: AgentExecutionRequest
    checkpoint: OmnigentCheckpointIdentity
    candidate_workspace: CandidateWorkspaceAuthority = Field(alias="candidateWorkspace")
    provider_lease: Mapping[str, Any] | None = Field(None, alias="providerLease")
    host_lease: Mapping[str, Any] | None = Field(None, alias="hostLease")
    host_registered: bool = Field(False, alias="hostRegistered")
    session_valid: bool = Field(False, alias="sessionValid")
    first_message_consistent: bool = Field(False, alias="firstMessageConsistent")
    current_credential_generation: int = Field(
        ..., alias="currentCredentialGeneration", ge=1
    )


class OmnigentCheckpointBranchRequest(BaseModel):
    """Input for the coordinator Checkpoint Branch execution path."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request: AgentExecutionRequest
    checkpoint: OmnigentCheckpointIdentity
    candidate_workspace: CandidateWorkspaceAuthority = Field(alias="candidateWorkspace")
    current_credential_generation: int = Field(
        ..., alias="currentCredentialGeneration", ge=1
    )


__all__ = [
    "OmnigentCheckpointBranchRequest",
    "OmnigentCheckpointRecoveryRequest",
]
