"""Credential-maintenance lease helpers for HTTP/service boundaries."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)

logger = logging.getLogger(__name__)


#: Upper bound for one profile host drain during credential enrollment. The
#: enrollment drawer reports "validating token" until the HTTP request
#: answers, so an unbounded janitor wait wedges the UI with no feedback.
#: Exceeding the bound fails closed with a retryable 503 that reuses the
#: deterministic lease owner; rotation stays atomic.
CREDENTIAL_DRAIN_TIMEOUT_SECONDS = 120.0

#: Upper bound for one best-effort maintenance-status query. Status polling
#: must never break enrollment, so the query degrades to unknown instead of
#: raising.
CREDENTIAL_STATUS_QUERY_TIMEOUT_SECONDS = 10.0


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
    *,
    profile_id: str,
    operation_id: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run forced host cleanup on the Docker-capable activity fleet.

    Bounded: exceeding ``timeout_seconds`` raises ``TimeoutError`` so the
    HTTP caller fails closed with a retryable refusal instead of leaving
    the enrollment drawer on "validating token" forever.
    """

    from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    bound = (
        CREDENTIAL_DRAIN_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    try:
        client = await asyncio.wait_for(
            TemporalClientAdapter().get_client(), timeout=bound
        )
        handle = await asyncio.wait_for(
            client.start_workflow(
                "MoonMind.OmnigentOAuthHostJanitor",
                {"profile_id": profile_id, "force": True},
                id=f"omnigent-oauth-host-drain:{operation_id}",
                task_queue=get_workflow_task_queue(),
            ),
            timeout=bound,
        )
        result = await asyncio.wait_for(handle.result(), timeout=bound)
    except TimeoutError as exc:
        raise TimeoutError(
            f"profile host drain timed out after {bound:g}s "
            f"for profile {profile_id}"
        ) from exc
    return dict(result or {})


def maintenance_status_from_state(
    *,
    state: Any,
    runtime_id: str,
    profile_id: str,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Project one profile's maintenance queue position from manager state.

    Pure projection over the ``get_state`` query payload: waiter identities
    other than the caller's are never returned, only counts and the caller's
    own 1-based position.
    """

    unknown: dict[str, Any] = {
        "profile_id": profile_id,
        "runtime_id": runtime_id,
        "known": False,
        "exclusive_maintenance_waiters": None,
        "waiter_position": None,
        "lease_held": False,
        "execution_lease_count": None,
    }
    profiles = state.get("profiles") if isinstance(state, dict) else None
    row = profiles.get(profile_id) if isinstance(profiles, dict) else None
    if not isinstance(row, dict):
        return unknown
    waiters = row.get("exclusive_maintenance_waiters")
    queue = row.get("exclusive_maintenance_queue")
    position: int | None = None
    if owner_id is not None and isinstance(queue, list):
        for index, entry in enumerate(queue):
            if isinstance(entry, dict) and entry.get("ownerId") == owner_id:
                position = index + 1
                break
    held = False
    leases = row.get("current_leases")
    if owner_id is not None and isinstance(leases, list):
        held = owner_id in leases
    lease_count = row.get("execution_lease_count")
    return {
        "profile_id": profile_id,
        "runtime_id": runtime_id,
        "known": True,
        "exclusive_maintenance_waiters": (
            int(waiters) if isinstance(waiters, int) else None
        ),
        "waiter_position": position,
        "lease_held": held,
        "execution_lease_count": (
            int(lease_count) if isinstance(lease_count, int) else None
        ),
    }


async def query_credential_maintenance_status(
    *,
    runtime_id: str,
    profile_id: str,
    owner_id: str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Return a best-effort maintenance queue snapshot for status polling.

    Never raises for manager/Temporal failures: status polling must not
    break enrollment, so unavailability degrades to ``known: False`` and the
    drawer falls back to elapsed-time progress text.
    """

    from moonmind.workflows.temporal.client import (
        TemporalClientAdapter,
        query_workflow,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )

    bound = (
        CREDENTIAL_STATUS_QUERY_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        adapter = TemporalClientAdapter()
        client = await asyncio.wait_for(adapter.get_client(), timeout=bound)
        state = await asyncio.wait_for(
            query_workflow(client, workflow_id_for_runtime(runtime_id), "get_state"),
            timeout=bound,
        )
    except Exception:
        logger.warning(
            "Credential maintenance status unavailable: runtime_id=%s profile_id=%s",
            runtime_id,
            profile_id,
            exc_info=True,
        )
        return {
            "profile_id": profile_id,
            "runtime_id": runtime_id,
            "known": False,
            "exclusive_maintenance_waiters": None,
            "waiter_position": None,
            "lease_held": False,
            "execution_lease_count": None,
        }
    return maintenance_status_from_state(
        state=state,
        runtime_id=runtime_id,
        profile_id=profile_id,
        owner_id=owner_id,
    )


__all__ = [
    "CREDENTIAL_DRAIN_TIMEOUT_SECONDS",
    "CREDENTIAL_STATUS_QUERY_TIMEOUT_SECONDS",
    "CredentialMaintenanceGuard",
    "acquire_credential_maintenance_guard",
    "drain_profile_bound_hosts",
    "maintenance_status_from_state",
    "query_credential_maintenance_status",
]
