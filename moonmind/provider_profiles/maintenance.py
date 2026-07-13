"""Credential-maintenance lease helpers for HTTP/service boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)


@dataclass
class CredentialMaintenanceGuard:
    lease_client: ProviderProfileLeaseClient
    lease: CredentialLease

    async def release(self) -> None:
        await self.lease_client.release_lease(self.lease)


async def acquire_credential_maintenance_guard(
    *,
    runtime_id: str,
    profile_id: str,
    purpose: CredentialLeasePurpose,
    operation_id: str,
    metadata: dict[str, Any] | None = None,
) -> CredentialMaintenanceGuard:
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    owner_id = deterministic_lease_owner_id(
        profile_id=profile_id,
        purpose=purpose,
        idempotency_key=operation_id,
    )
    client = ProviderProfileLeaseClient(TemporalClientAdapter())
    lease = await client.acquire_maintenance_lease(
        runtime_id=runtime_id,
        profile_id=profile_id,
        owner_id=owner_id,
        purpose=purpose,
        metadata=metadata,
    )
    return CredentialMaintenanceGuard(client, lease)


async def drain_profile_bound_hosts(
    *, profile_id: str, operation_id: str
) -> dict[str, Any]:
    """Run forced host cleanup on the Docker-capable activity fleet."""

    from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    client = await TemporalClientAdapter().get_client()
    handle = await client.start_workflow(
        "MoonMind.OmnigentOAuthHostJanitor",
        {"profile_id": profile_id, "force": True},
        id=f"omnigent-oauth-host-drain:{operation_id}",
        task_queue=get_workflow_task_queue(),
    )
    result = await handle.result()
    return dict(result or {})


__all__ = [
    "CredentialMaintenanceGuard",
    "acquire_credential_maintenance_guard",
    "drain_profile_bound_hosts",
]
