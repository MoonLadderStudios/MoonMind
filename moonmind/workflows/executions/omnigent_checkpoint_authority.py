"""Compile admitted checkpoint evidence into controller-owned Omnigent input."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointExecutionInput,
    OmnigentCheckpointIdentity,
)
from moonmind.schemas.temporal_models import WorkspaceCheckpointEvidenceModel


class OmnigentCheckpointAuthorityError(ValueError):
    """Bounded failure raised when durable evidence cannot authorize recovery."""


def compile_omnigent_checkpoint_execution(
    *,
    recovery_workspace: Mapping[str, Any],
    validation_ref: str,
    action: str = "resume",
    current_authority: Mapping[str, Any] | None = None,
) -> OmnigentCheckpointExecutionInput:
    """Build Activity input from admitted server-side evidence.

    ``current_authority`` is deliberately a controller-only input.  When it is
    absent, live authority claims are false and the coordinator can only choose
    cold restore.
    """

    raw_workspace = recovery_workspace.get("workspaceCheckpoint")
    if raw_workspace is None:
        raw_workspace = recovery_workspace
    raw_candidate = recovery_workspace.get("candidateWorkspace")
    if not isinstance(raw_workspace, Mapping) or not isinstance(
        raw_candidate, Mapping
    ):
        raise OmnigentCheckpointAuthorityError(
            "resume_unavailable:checkpoint_authority_missing"
        )
    try:
        workspace = WorkspaceCheckpointEvidenceModel.model_validate(raw_workspace)
        candidate = CandidateWorkspaceAuthority.model_validate(raw_candidate)
    except ValidationError as exc:
        raise OmnigentCheckpointAuthorityError(
            "resume_unavailable:checkpoint_evidence_invalid"
        ) from exc

    required = {
        "providerProfileId": workspace.provider_profile_id,
        "credentialGeneration": workspace.credential_generation,
        "hostBindingRef": workspace.host_binding_ref,
        "endpointRef": workspace.endpoint_ref,
        "bridgeSessionId": workspace.bridge_session_id,
        "externalStateRef": workspace.external_state_ref,
        "idempotencyKey": workspace.idempotency_key,
    }
    if any(value is None for value in required.values()):
        raise OmnigentCheckpointAuthorityError(
            "resume_unavailable:checkpoint_identity_missing"
        )
    if candidate.checkpoint_ref not in {
        workspace.external_state_ref,
        workspace.manifest_ref,
        workspace.workspace_artifact_ref,
    }:
        raise OmnigentCheckpointAuthorityError(
            "resume_unavailable:checkpoint_identity_mismatch"
        )

    authority = dict(current_authority or {})
    try:
        return OmnigentCheckpointExecutionInput(
            action=action,
            checkpoint=OmnigentCheckpointIdentity(
                providerProfileId=workspace.provider_profile_id,
                credentialGeneration=workspace.credential_generation,
                providerLeaseRef=workspace.provider_lease_ref,
                hostBindingRef=workspace.host_binding_ref,
                hostLeaseRef=workspace.host_lease_ref,
                endpointRef=workspace.endpoint_ref,
                omnigentHostId=workspace.omnigent_host_id,
                omnigentSessionId=workspace.omnigent_session_id,
                bridgeSessionId=workspace.bridge_session_id,
                externalStateRef=workspace.external_state_ref,
                idempotencyKey=workspace.idempotency_key,
                terminalRef=workspace.terminal_ref,
                diagnosticsRef=workspace.diagnostics_ref,
            ),
            candidateWorkspace=candidate,
            currentCredentialGeneration=(
                authority.get("currentCredentialGeneration")
                or workspace.credential_generation
            ),
            providerLease=authority.get("providerLease"),
            hostLease=authority.get("hostLease"),
            hostRegistered=authority.get("hostRegistered", False),
            sessionValid=authority.get("sessionValid", False),
            firstMessageConsistent=authority.get(
                "firstMessageConsistent", False
            ),
            eventCursorValid=authority.get("eventCursorValid", False),
            workspaceAuthorityValid=True,
            policyValid=True,
            originalInputUnchanged=action == "resume",
            validationRef=validation_ref,
        )
    except ValidationError as exc:
        raise OmnigentCheckpointAuthorityError(
            "resume_unavailable:checkpoint_evidence_invalid"
        ) from exc


__all__ = [
    "OmnigentCheckpointAuthorityError",
    "compile_omnigent_checkpoint_execution",
]
