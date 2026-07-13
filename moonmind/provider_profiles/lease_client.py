"""Shared Provider Profile credential-capacity lease client.

The ProviderProfileManager is the single capacity ledger for direct execution,
Omnigent execution, and credential maintenance. This module is intentionally an
activity/service boundary client; workflow code uses deterministic handles.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class CredentialLeasePurpose(str, Enum):
    EXECUTION_DIRECT = "execution_direct"
    EXECUTION_OMNIGENT = "execution_omnigent"
    OAUTH_CONNECT = "oauth_connect"
    OAUTH_RECONNECT = "oauth_reconnect"
    OAUTH_DISCONNECT = "oauth_disconnect"
    CREDENTIAL_VALIDATION = "credential_validation"
    CREDENTIAL_REPAIR = "credential_repair"

    @property
    def is_maintenance(self) -> bool:
        return self not in {
            CredentialLeasePurpose.EXECUTION_DIRECT,
            CredentialLeasePurpose.EXECUTION_OMNIGENT,
        }


def deterministic_lease_owner_id(
    *,
    profile_id: str,
    purpose: CredentialLeasePurpose | str,
    workflow_id: str | None = None,
    step_execution_id: str | None = None,
    oauth_session_id: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    """Return a stable owner ID so an activity retry reuses one lease."""

    normalized_purpose = CredentialLeasePurpose(purpose).value
    identity = "\x1f".join(
        str(value or "").strip()
        for value in (
            profile_id,
            normalized_purpose,
            workflow_id,
            step_execution_id,
            oauth_session_id,
            idempotency_key,
        )
    )
    # Use a domain-separated slow derivation rather than a plain hash. Some
    # identity inputs originate at credential-sensitive boundaries; the
    # resulting owner token must be stable for activity retries without
    # exposing a reusable raw hash of those identifiers.
    digest = hashlib.scrypt(
        identity.encode("utf-8"),
        salt=b"moonmind-provider-profile-lease-owner-v1",
        n=2**14,
        r=8,
        p=1,
        dklen=16,
    ).hex()
    return f"profile-lease:{normalized_purpose}:{digest}"


@dataclass(frozen=True)
class CredentialLease:
    profile_id: str
    runtime_id: str
    lease_id: str
    owner_id: str
    purpose: CredentialLeasePurpose
    already_held: bool = False


class ProviderProfileLeaseClient:
    """Activity-boundary client for the shared Provider Profile manager."""

    def __init__(self, temporal_adapter: Any) -> None:
        self._adapter = temporal_adapter

    async def _ensure_manager(self, runtime_id: str) -> str:
        from temporalio.exceptions import WorkflowAlreadyStartedError

        from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
        from moonmind.workflows.temporal.workflows.provider_profile_manager import (
            WORKFLOW_NAME,
            workflow_id_for_runtime,
        )

        workflow_id = workflow_id_for_runtime(runtime_id)
        client = await self._adapter.get_client()
        try:
            await client.start_workflow(
                WORKFLOW_NAME,
                {"runtime_id": runtime_id},
                id=workflow_id,
                task_queue=get_workflow_task_queue(),
            )
        except WorkflowAlreadyStartedError:
            # A concurrent caller already established the shared manager.
            pass
        return workflow_id

    async def _acquire(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        purpose: CredentialLeasePurpose,
        metadata: Mapping[str, Any] | None,
    ) -> CredentialLease:
        workflow_id = await self._ensure_manager(runtime_id)
        safe_metadata = dict(metadata or {})
        # This client runs at an Activity/HTTP boundary. Its deterministic
        # owner is an idempotency identity, not a Temporal workflow ID.
        safe_metadata["ownerIsWorkflow"] = False
        result = await self._adapter.update_workflow(
            workflow_id,
            (
                "AcquireCredentialMaintenanceLease"
                if purpose.is_maintenance
                else "AcquireSlot"
            ),
            {
                "requester_workflow_id": owner_id,
                "owner_id": owner_id,
                "runtime_id": runtime_id,
                "execution_profile_ref": profile_id,
                "purpose": purpose.value,
                "metadata": safe_metadata,
            },
        )
        return CredentialLease(
            profile_id=str(result["profile_id"]),
            runtime_id=runtime_id,
            lease_id=str(result["lease_id"]),
            owner_id=owner_id,
            purpose=purpose,
            already_held=bool(result.get("already_held")),
        )

    async def acquire_execution_lease(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        purpose: CredentialLeasePurpose = CredentialLeasePurpose.EXECUTION_DIRECT,
        metadata: Mapping[str, Any] | None = None,
    ) -> CredentialLease:
        if purpose.is_maintenance:
            raise ValueError("execution lease requires an execution purpose")
        return await self._acquire(
            runtime_id=runtime_id,
            profile_id=profile_id,
            owner_id=owner_id,
            purpose=purpose,
            metadata=metadata,
        )

    async def acquire_maintenance_lease(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        purpose: CredentialLeasePurpose,
        metadata: Mapping[str, Any] | None = None,
    ) -> CredentialLease:
        if not purpose.is_maintenance:
            raise ValueError("maintenance lease requires a maintenance purpose")
        return await self._acquire(
            runtime_id=runtime_id,
            profile_id=profile_id,
            owner_id=owner_id,
            purpose=purpose,
            metadata=metadata,
        )

    async def release_lease(self, lease: CredentialLease) -> None:
        await self._adapter.signal_workflow(
            await self._ensure_manager(lease.runtime_id),
            "release_slot",
            {
                "requester_workflow_id": lease.owner_id,
                "owner_id": lease.owner_id,
                "runtime_id": lease.runtime_id,
                "profile_id": lease.profile_id,
                "lease_id": lease.lease_id,
                "purpose": lease.purpose.value,
            },
        )

    async def record_cooldown(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        cooldown_seconds: int,
        reason: str,
    ) -> None:
        await self._adapter.signal_workflow(
            await self._ensure_manager(runtime_id),
            "report_cooldown",
            {
                "profile_id": profile_id,
                "requester_workflow_id": owner_id,
                "cooldown_seconds": cooldown_seconds,
                "reason": reason,
            },
        )

    async def inspect_lease(self, lease: CredentialLease) -> dict[str, Any]:
        return await self._adapter.update_workflow(
            await self._ensure_manager(lease.runtime_id),
            "InspectCredentialLease",
            {"lease_id": lease.lease_id, "owner_id": lease.owner_id},
        )


__all__ = [
    "CredentialLease",
    "CredentialLeasePurpose",
    "ProviderProfileLeaseClient",
    "deterministic_lease_owner_id",
]
