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

    @property
    def consumes_provider_capacity(self) -> bool:
        """Whether this purpose spends the upstream provider/host resource.

        MoonLadderStudios/MoonMind#3879: shared-scope fullness and cooldown are
        statements about the upstream resource, so they may only gate work that
        actually spends it. Credential repair and revocation are local
        credential-state operations; blocking them behind a saturated or
        cooling-down scope would leave a broken credential unrepairable exactly
        when the scope is under the most pressure.
        """

        return self not in {
            CredentialLeasePurpose.CREDENTIAL_REPAIR,
            CredentialLeasePurpose.OAUTH_DISCONNECT,
        }


class CredentialLeaseMode(str, Enum):
    """How one acquisition interacts with a Provider Profile's capacity ledger.

    MoonLadderStudios/MoonMind#3878: configured capacity ``N`` is a shared
    execution ceiling. Validation that touches no shared credential state must
    not require capacity one, and exclusive credential maintenance must both
    block new consumers and wait for existing ones to drain.
    """

    #: Counts against the profile's effective execution ceiling.
    SHARED_EXECUTION = "shared_execution"
    #: One in-flight holder per exact evidence identity; consumes no execution
    #: slot and imposes no capacity-one requirement.
    SINGLE_FLIGHT_VALIDATION = "single_flight_validation"
    #: Blocks new consumers and drains existing credential consumers first.
    EXCLUSIVE_MAINTENANCE = "exclusive_maintenance"


#: Error type the manager raises when it detaches an accepted Update so it can
#: Continue-As-New. The request itself stays durable and ordered; the client is
#: expected to reattach by resubmitting the identical owner request.
MANAGER_ROLLOVER_ERROR_TYPE = "ProviderProfileManagerRollover"

#: Bounded reattach budget: one clean-completion race plus one rollover detach.
_MAX_MANAGER_UPDATE_ATTEMPTS = 3


#: A Provider Profile whose credential source is ``none`` materializes no
#: shared, mutable authentication state, so validating its model evidence
#: cannot corrupt a concurrent execution.
CREDENTIALLESS_CREDENTIAL_SOURCES = frozenset({"none"})


def credential_source_is_credentialless(credential_source: Any) -> bool:
    """Return whether a profile owns no shared mutable credential state."""

    value = getattr(credential_source, "value", credential_source)
    return str(value or "").strip().lower() in CREDENTIALLESS_CREDENTIAL_SOURCES


def credential_lease_mode(
    *,
    purpose: CredentialLeasePurpose | str,
    credentialless: bool,
) -> CredentialLeaseMode:
    """Derive the canonical capacity-ledger mode for one lease acquisition.

    The mode is a function of the declared purpose and the profile's durable
    credential contract only. Callers never choose it, so no caller can widen
    its own authority by naming a weaker mode.
    """

    normalized = CredentialLeasePurpose(purpose)
    if not normalized.is_maintenance:
        return CredentialLeaseMode.SHARED_EXECUTION
    if (
        normalized is CredentialLeasePurpose.CREDENTIAL_VALIDATION
        and credentialless
    ):
        return CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION
    return CredentialLeaseMode.EXCLUSIVE_MAINTENANCE


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
    #: Ledger mode the manager actually applied. ``None`` only for leases
    #: reconstructed from a persisted handle, where the mode is not replayed.
    mode: CredentialLeaseMode | None = None
    #: Monotonic grant generation the manager assigned. Released leases quote
    #: it back so a duplicate or delayed release cannot free the *next* holder
    #: of the same deterministic owner ID. ``None`` for a manager whose history
    #: predates fenced grants, and for a lease reconstructed from a persisted
    #: handle written before the generation was recorded; the manager honours
    #: an unfenced release so those handles still free capacity.
    fencing_generation: int | None = None
    #: Compact versioned identity this lease was granted against, when the
    #: caller declared one. The manager rejects a second request that reuses
    #: the owner ID under a different identity.
    evidence_identity: str | None = None


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
        owner_is_workflow: bool,
    ) -> CredentialLease:
        safe_metadata = dict(metadata or {})
        # Most callers use an activity idempotency identity. A workflow may
        # delegate an acknowledged Update through an Activity when the
        # workflow-context ExternalWorkflowHandle cannot execute Updates; in
        # that case the stable owner really is the delegating workflow ID.
        safe_metadata["ownerIsWorkflow"] = owner_is_workflow
        result = await self._update_manager(
            runtime_id,
            (
                "AcquireCredentialMaintenanceLease"
                if purpose.is_maintenance
                else "AcquireSlotV2"
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
        raw_mode = str(result.get("lease_mode") or "").strip()
        raw_fence = result.get("lease_fencing_generation")
        try:
            fencing_generation = int(raw_fence) if raw_fence is not None else None
        except (TypeError, ValueError):
            fencing_generation = None
        return CredentialLease(
            profile_id=str(result["profile_id"]),
            runtime_id=runtime_id,
            lease_id=str(result["lease_id"]),
            owner_id=owner_id,
            purpose=purpose,
            already_held=bool(result.get("already_held")),
            mode=CredentialLeaseMode(raw_mode) if raw_mode else None,
            fencing_generation=fencing_generation,
            evidence_identity=(
                str(safe_metadata["evidenceIdentity"])
                if safe_metadata.get("evidenceIdentity") is not None
                else None
            ),
        )

    async def _update_manager(
        self,
        runtime_id: str,
        update_name: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Retry an Update that races the manager's clean completion or rollover.

        Ensuring and updating are two distinct Temporal RPCs. The manager can
        complete after ``start_workflow`` reports it running but before the
        accepted Update resolves. Re-ensuring the canonical workflow ID and
        resubmitting the same idempotent owner request closes that race without
        consuming the caller Activity retry.

        MoonLadderStudios/MoonMind#3879 adds the second case: the manager
        detaches waiting maintenance handlers before Continue-As-New so the
        rollover is not blocked by an unbounded wait. The waiter's queue entry
        and its position are durable across the rollover, so resubmitting the
        same owner request reattaches to the *same* pending request instead of
        losing its turn. This is the client half of that protocol.
        """

        from temporalio.client import WorkflowUpdateFailedError
        from temporalio.exceptions import ApplicationError

        reattachable = {
            "AcceptedUpdateCompletedWorkflow",
            MANAGER_ROLLOVER_ERROR_TYPE,
        }
        for attempt in range(_MAX_MANAGER_UPDATE_ATTEMPTS):
            workflow_id = await self._ensure_manager(runtime_id)
            try:
                return await self._adapter.update_workflow(
                    workflow_id,
                    update_name,
                    dict(payload),
                )
            except WorkflowUpdateFailedError as exc:
                cause = exc.cause
                reattach = bool(
                    isinstance(cause, ApplicationError) and cause.type in reattachable
                )
                if attempt + 1 < _MAX_MANAGER_UPDATE_ATTEMPTS and reattach:
                    continue
                raise
        raise AssertionError("bounded manager update retry did not terminate")

    async def acquire_execution_lease(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        purpose: CredentialLeasePurpose = CredentialLeasePurpose.EXECUTION_DIRECT,
        metadata: Mapping[str, Any] | None = None,
        owner_is_workflow: bool = False,
    ) -> CredentialLease:
        if purpose.is_maintenance:
            raise ValueError("execution lease requires an execution purpose")
        return await self._acquire(
            runtime_id=runtime_id,
            profile_id=profile_id,
            owner_id=owner_id,
            purpose=purpose,
            metadata=metadata,
            owner_is_workflow=owner_is_workflow,
        )

    async def acquire_maintenance_lease(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner_id: str,
        purpose: CredentialLeasePurpose,
        metadata: Mapping[str, Any] | None = None,
        owner_is_workflow: bool = False,
    ) -> CredentialLease:
        if not purpose.is_maintenance:
            raise ValueError("maintenance lease requires a maintenance purpose")
        return await self._acquire(
            runtime_id=runtime_id,
            profile_id=profile_id,
            owner_id=owner_id,
            purpose=purpose,
            metadata=metadata,
            owner_is_workflow=owner_is_workflow,
        )

    async def release_lease(self, lease: CredentialLease) -> None:
        payload: dict[str, Any] = {
            "requester_workflow_id": lease.owner_id,
            "owner_id": lease.owner_id,
            "runtime_id": lease.runtime_id,
            "profile_id": lease.profile_id,
            "lease_id": lease.lease_id,
            "purpose": lease.purpose.value,
        }
        if lease.fencing_generation is not None:
            # Fence the release to the exact grant this handle owns. A retry
            # that re-sends this signal after the owner ID was granted again
            # must not release the replacement holder.
            payload["fencing_generation"] = lease.fencing_generation
        await self._adapter.signal_workflow(
            await self._ensure_manager(lease.runtime_id),
            "release_slot",
            payload,
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
        return dict(
            await self._update_manager(
                lease.runtime_id,
                "InspectCredentialLease",
                {"lease_id": lease.lease_id, "owner_id": lease.owner_id},
            )
        )


__all__ = [
    "CREDENTIALLESS_CREDENTIAL_SOURCES",
    "MANAGER_ROLLOVER_ERROR_TYPE",
    "CredentialLease",
    "CredentialLeaseMode",
    "CredentialLeasePurpose",
    "ProviderProfileLeaseClient",
    "credential_lease_mode",
    "credential_source_is_credentialless",
    "deterministic_lease_owner_id",
]
