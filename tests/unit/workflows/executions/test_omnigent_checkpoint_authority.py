from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.executions.omnigent_checkpoint_authority import (
    OmnigentCheckpointAuthorityError,
    compile_omnigent_checkpoint_execution,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


def _workspace() -> dict[str, object]:
    checkpoint_ref = "artifact://omnigent/state/1"
    return {
        "workspaceCheckpoint": {
            "kind": "external_state_ref",
            "externalStateRef": checkpoint_ref,
            "idempotencyKey": "source-attempt",
            "firstMessageDigest": "sha256:" + "c" * 64,
            "eventCursorRef": "artifact://events/1",
            "omnigentSessionId": "session-1",
            "providerProfileId": "codex",
            "credentialGeneration": 3,
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "binding-1",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "endpoint-1",
            "omnigentHostId": "host-1",
            "bridgeSessionId": "bridge-1",
        },
        "candidateWorkspace": {
            "loopId": "loop-1",
            "attemptOrdinal": 2,
            "headRef": "artifact://head/2",
            "headDigest": "sha256:" + "a" * 64,
            "checkpointRef": checkpoint_ref,
            "checkpointDigest": "sha256:" + "b" * 64,
        },
    }


def test_compiler_rejects_missing_current_authority() -> None:
    with pytest.raises(
        OmnigentCheckpointAuthorityError,
        match="resume_unavailable:checkpoint_evidence_invalid",
    ):
        compile_omnigent_checkpoint_execution(
            recovery_workspace=_workspace(),
            validation_ref="artifact://validation/1",
        )


def test_compiler_accepts_resolved_cold_restore_authority() -> None:
    execution = compile_omnigent_checkpoint_execution(
        recovery_workspace=_workspace(),
        validation_ref="artifact://validation/1",
        current_authority={
            "currentCredentialGeneration": 3,
            "workspaceAuthorityValid": True,
            "policyValid": True,
        },
    )

    assert execution.provider_lease is None
    assert execution.host_lease is None
    assert execution.host_registered is False
    assert execution.session_valid is False


def test_compiler_accepts_controller_resolved_live_authority() -> None:
    execution = compile_omnigent_checkpoint_execution(
        recovery_workspace=_workspace(),
        validation_ref="artifact://validation/1",
        current_authority={
            "currentCredentialGeneration": 3,
            "providerLease": {
                "active": True,
                "leaseId": "provider-lease-1",
            },
            "hostLease": {
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 3,
            },
            "hostRegistered": True,
            "sessionValid": True,
            "firstMessageConsistent": True,
            "eventCursorValid": True,
            "workspaceAuthorityValid": True,
            "policyValid": True,
        },
    )

    assert execution.provider_lease == {
        "active": True,
        "leaseId": "provider-lease-1",
    }
    assert execution.host_registered is True


def test_compiler_rejects_mismatched_checkpoint_authority() -> None:
    workspace = _workspace()
    workspace["candidateWorkspace"]["checkpointRef"] = "artifact://other"  # type: ignore[index]

    with pytest.raises(
        OmnigentCheckpointAuthorityError,
        match="resume_unavailable:checkpoint_identity_mismatch",
    ):
        compile_omnigent_checkpoint_execution(
            recovery_workspace=workspace,
            validation_ref="artifact://validation/1",
        )


@pytest.mark.asyncio
async def test_service_resolves_live_authority_from_current_owners() -> None:
    service = object.__new__(TemporalExecutionService)
    service._session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: SimpleNamespace(expires_at=None)
            )
        ),
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(credential_generation=3),
                SimpleNamespace(
                    provider_profile_id="codex",
                    provider_lease_id="provider-lease-1",
                    binding_ref="binding-1",
                    lease_id="host-lease-1",
                    credential_generation=3,
                    omnigent_host_id="host-1",
                    omnigent_session_id="session-1",
                    bridge_session_id="bridge-1",
                    disconnected_at=None,
                    status="assigned",
                    host_readiness="ready",
                ),
                SimpleNamespace(
                    provider_profile_id="codex",
                    provider_lease_id="provider-lease-1",
                    credential_generation=3,
                    host_binding_ref="binding-1",
                    host_lease_ref="host-lease-1",
                    omnigent_endpoint_ref="endpoint-1",
                    omnigent_host_id="host-1",
                    omnigent_session_id="session-1",
                    external_state_ref="artifact://omnigent/state/1",
                    first_message_state="posted",
                    first_message_digest="sha256:" + "c" * 64,
                    idempotency_key="source-attempt",
                    normalized_events_ref="artifact://events/1",
                    raw_events_ref=None,
                ),
            ]
        )
    )
    admitted = SimpleNamespace(admitted=True)

    authority = await service._resolve_omnigent_checkpoint_authority(
        recovery_workspace=_workspace(),
        admitted_decision=admitted,
    )

    assert authority["currentCredentialGeneration"] == 3
    assert authority["providerLease"]["active"] is True
    assert authority["hostRegistered"] is True
    assert authority["sessionValid"] is True
    assert authority["firstMessageConsistent"] is True
    assert authority["eventCursorValid"] is True
    assert authority["workspaceAuthorityValid"] is True
    assert authority["policyValid"] is True
    assert authority["authorityRationale"] == ()


@pytest.mark.asyncio
async def test_service_fails_live_authority_closed_on_stale_owner_and_identity() -> None:
    service = object.__new__(TemporalExecutionService)
    service._session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ),
        get=AsyncMock(
            side_effect=[
                SimpleNamespace(credential_generation=3),
                SimpleNamespace(
                    provider_profile_id="codex",
                    provider_lease_id="provider-lease-1",
                    binding_ref="binding-1",
                    lease_id="host-lease-1",
                    credential_generation=3,
                    omnigent_host_id="host-1",
                    omnigent_session_id="session-1",
                    bridge_session_id="bridge-1",
                    disconnected_at=None,
                    status="assigned",
                    host_readiness="ready",
                ),
                SimpleNamespace(
                    provider_profile_id="codex",
                    provider_lease_id="provider-lease-1",
                    credential_generation=3,
                    host_binding_ref="binding-1",
                    host_lease_ref="host-lease-1",
                    omnigent_endpoint_ref="endpoint-1",
                    omnigent_host_id="host-1",
                    omnigent_session_id="session-1",
                    external_state_ref="artifact://omnigent/state/1",
                    first_message_state="posted",
                    first_message_digest="sha256:" + "d" * 64,
                    idempotency_key="source-attempt",
                    normalized_events_ref="artifact://events/stale",
                    raw_events_ref=None,
                ),
            ]
        ),
    )

    authority = await service._resolve_omnigent_checkpoint_authority(
        recovery_workspace=_workspace(),
        admitted_decision=SimpleNamespace(admitted=True),
    )

    assert authority["providerLease"] is None
    assert authority["firstMessageConsistent"] is False
    assert authority["eventCursorValid"] is False
    assert authority["authorityRationale"] == (
        "provider_lease_inactive",
        "first_message_identity_mismatch",
        "event_cursor_identity_mismatch",
    )
