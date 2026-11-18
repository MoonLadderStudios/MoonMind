"""Collect one provider account-usage observation under credential authority.

Started by the API's account-usage dispatcher for one refresh attempt
(workflow ID ``provider-account-usage:<profile>:<attempt>``). It routes the
attempt to the credential's active owner, or takes a non-blocking idle grant
from the Provider Profile manager and runs one bounded probe. If execution or
maintenance needs the credential home meanwhile, the manager signals
``preempt_usage_observation``: the probe is cancelled, its container is
proven gone, and only then is the grant released (release-last ordering).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy
    from temporalio.workflow import ActivityCancellationType

    from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        USAGE_OBSERVATION_PREEMPT_SIGNAL,
        workflow_id_for_runtime,
    )

WORKFLOW_NAME = "MoonMind.ProviderAccountUsageCollection"
WORKFLOW_ID_PREFIX = "provider-account-usage"
ACTIVITY_TASK_QUEUE = "mm.activity.agent_runtime"
_ONCE = RetryPolicy(maximum_attempts=1)
_BOUNDED = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)


def workflow_id_for_attempt(profile_id: str, attempt_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}:{profile_id}:{attempt_id}"


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindProviderAccountUsageCollectionWorkflow:
    def __init__(self) -> None:
        self._preempted = False

    @workflow.signal(name=USAGE_OBSERVATION_PREEMPT_SIGNAL)
    def preempt_usage_observation(self, payload: dict[str, Any]) -> None:
        self._preempted = True

    async def _activity(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
        retry: RetryPolicy = _BOUNDED,
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            name,
            payload,
            task_queue=ACTIVITY_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=timeout_seconds),
            retry_policy=retry,
        )

    async def _release(self, request: dict[str, Any], lease: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "requester_workflow_id": lease["leaseId"],
            "owner_id": lease["leaseId"],
            "runtime_id": request["runtime_id"],
            "profile_id": request["profile_id"],
            "lease_id": lease["leaseId"],
            "purpose": CredentialLeasePurpose.USAGE_OBSERVATION.value,
        }
        if lease.get("fencingGeneration") is not None:
            payload["fencing_generation"] = int(lease["fencingGeneration"])
        await workflow.get_external_workflow_handle(
            workflow_id_for_runtime(request["runtime_id"])
        ).signal("release_slot", payload)

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        resolved = await self._activity(
            "provider_account_usage.resolve", request, timeout_seconds=60
        )
        if resolved.get("status") != "ready":
            return {"status": resolved.get("status")}
        if resolved["route"] == "active_owner":
            return await self._activity(
                "provider_account_usage.collect_from_owner",
                {**request, "owners": resolved["owners"]},
                timeout_seconds=150,
                retry=_ONCE,
            )

        owner_id = workflow.info().workflow_id
        lease = {"leaseId": owner_id, "fencingGeneration": None}
        try:
            grant = await self._activity(
                "provider_account_usage.try_acquire",
                {
                    **request,
                    "owner_id": owner_id,
                    "credentialResourceKey": resolved["credentialResourceKey"],
                    "aliasProfileIds": resolved["aliasProfileIds"],
                },
                timeout_seconds=60,
            )
        except exceptions.ActivityError:
            # The grant may exist without an acknowledgment. No probe ran
            # under it, so releasing it is safe.
            await self._release(request, lease)
            raise
        if not grant.get("granted"):
            return {"status": grant.get("status") or "deferred"}
        lease["fencingGeneration"] = grant.get("fencingGeneration")

        outcome: dict[str, Any] = {"status": "failed"}
        cleaned = False
        try:
            probe = workflow.start_activity(
                "provider_account_usage.probe",
                {**request, **lease},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=240),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=_ONCE,
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            await workflow.wait_condition(lambda: probe.done() or self._preempted)
            if not probe.done():
                probe.cancel()
            try:
                outcome = await probe
            except (exceptions.ActivityError, exceptions.CancelledError):
                outcome = {"status": "deferred" if self._preempted else "failed"}
        finally:
            try:
                await self._activity(
                    "provider_account_usage.cleanup",
                    {**request, **lease, "deferAttempt": self._preempted},
                    timeout_seconds=90,
                )
                cleaned = True
            except exceptions.ActivityError:
                # Without proof of cleanup the grant stays held; the manager
                # reclaims it only once this run is closed and the probe's
                # own lifetime has passed.
                outcome = {"status": "failed", "cleanup": "unverified"}
            if cleaned:
                await self._release(request, lease)
        return {"status": outcome.get("status"), "preempted": self._preempted}


__all__ = [
    "MoonMindProviderAccountUsageCollectionWorkflow",
    "WORKFLOW_ID_PREFIX",
    "WORKFLOW_NAME",
    "workflow_id_for_attempt",
]
