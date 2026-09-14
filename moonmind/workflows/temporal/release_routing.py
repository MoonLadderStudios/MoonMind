"""Temporal owns release routing; startup converges abandoned routing to the deployed release."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from uuid import uuid4

from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
)
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion
from temporalio.service import RPCError, RPCStatusCode

logger = logging.getLogger(__name__)


async def routing_snapshot(client, deployment: str):
    return await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(
            namespace=client.namespace, deployment_name=deployment
        )
    )


def current_version(snapshot) -> str:
    routing = snapshot.worker_deployment_info.routing_config
    value = routing.current_version
    if value:
        return value
    current = routing.current_deployment_version
    return f"{current.deployment_name}.{current.build_id}" if current.build_id else ""


def ramping_version(snapshot) -> str:
    routing = snapshot.worker_deployment_info.routing_config
    if routing.ramping_version:
        return routing.ramping_version
    ramping = routing.ramping_deployment_version
    return f"{ramping.deployment_name}.{ramping.build_id}" if ramping.build_id else ""


async def version_availability(
    client, version: str, *, live_container_ids=None
) -> dict:
    """Observe pollers at the deployment-aware (legacy) task-queue API.

    Enhanced Build-ID selection does not expose Worker Deployment pollers on
    Temporal 1.29. Docker inventory additionally disproves cached pollers from
    containers removed since the server's last poll observation.
    """
    import re
    from datetime import datetime, timezone
    from temporalio.api.enums.v1 import DescribeTaskQueueMode
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    description = await client.workflow_service.describe_worker_deployment_version(
        DescribeWorkerDeploymentVersionRequest(
            namespace=client.namespace, version=version
        )
    )
    queues = []
    for queue in description.worker_deployment_version_info.task_queue_infos:
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=queue.name),
                api_mode=DescribeTaskQueueMode.DESCRIBE_TASK_QUEUE_MODE_UNSPECIFIED,
                task_queue_type=queue.type,
                report_pollers=True,
                report_stats=True,
            )
        )
        pollers = []
        for poller in response.pollers:
            options = poller.deployment_options
            if f"{options.deployment_name}.{options.build_id}" != version:
                continue
            host = poller.identity.partition("@")[2]
            if (
                live_container_ids is not None
                and re.fullmatch(r"[0-9a-f]{12,64}", host)
                and not any(
                    identifier.startswith(host) for identifier in live_container_ids
                )
            ):
                continue
            age = (
                datetime.now(timezone.utc)
                - poller.last_access_time.ToDatetime(tzinfo=timezone.utc)
            ).total_seconds()
            if age <= 90:
                pollers.append(poller.identity)
        queues.append(
            {
                "queue": queue.name,
                "type": queue.type,
                "livePollers": len(pollers),
                "pendingAgeSeconds": response.stats.approximate_backlog_age.ToTimedelta().total_seconds(),
            }
        )
    return {
        "version": version,
        "available": bool(queues) and all(item["livePollers"] for item in queues),
        "queues": queues,
    }


async def version_drained(client, version: str) -> bool:
    """Only Temporal's terminal drainage evidence releases old pollers."""
    from temporalio.api.enums.v1 import VersionDrainageStatus

    try:
        response = await client.workflow_service.describe_worker_deployment_version(
            DescribeWorkerDeploymentVersionRequest(
                namespace=client.namespace, version=version
            )
        )
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return (
        response.worker_deployment_version_info.drainage_info.status
        == VersionDrainageStatus.VERSION_DRAINAGE_STATUS_DRAINED
    )


async def qualification_closed_without_activation(
    client, *, version: str, canary_id: str
) -> bool:
    """A private candidate's only admitted workflow is its named canary.

    Inactive versions never enter Temporal's drainage state machine. They can
    retire after the release owner is terminal, its canary is closed (or was
    never started), and the service confirms this version never took traffic.
    """
    from temporalio.api.enums.v1 import WorkerDeploymentVersionStatus
    from temporalio.client import WorkflowExecutionStatus

    try:
        response = await client.workflow_service.describe_worker_deployment_version(
            DescribeWorkerDeploymentVersionRequest(
                namespace=client.namespace, version=version
            )
        )
        if (
            response.worker_deployment_version_info.status
            != WorkerDeploymentVersionStatus.WORKER_DEPLOYMENT_VERSION_STATUS_INACTIVE
        ):
            return False
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
    try:
        execution = await client.get_workflow_handle(canary_id).describe()
        return execution.status not in {
            WorkflowExecutionStatus.RUNNING,
            WorkflowExecutionStatus.CONTINUED_AS_NEW,
        }
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        return True


async def await_registered_queues(
    client, *, version, workflow_queue, activity_queues, workflow_queues=()
):
    """Do not admit a pinned canary until the service knows every target queue."""
    from temporalio.api.enums.v1 import TaskQueueType

    required = {
        (queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)
        for queue in (workflow_queues or (workflow_queue,))
    }
    required.update(
        (queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY) for queue in activity_queues
    )
    for attempt in range(60):
        try:
            response = await client.workflow_service.describe_worker_deployment_version(
                DescribeWorkerDeploymentVersionRequest(
                    namespace=client.namespace, version=version
                )
            )
            registered = {
                (item.name, item.type)
                for item in response.worker_deployment_version_info.task_queue_infos
            }
            if required <= registered:
                return
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
        await asyncio.sleep(1)
    raise RuntimeError(
        "Temporal did not register every candidate workflow and Activity queue"
    )


async def verify_ordinary_route(client, *, version, canary_id, timeout_seconds=120):
    """Prove ordinary unpinned traffic on every registered workflow queue."""
    import hashlib
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    response = await client.workflow_service.describe_worker_deployment_version(
        DescribeWorkerDeploymentVersionRequest(
            namespace=client.namespace, version=version
        )
    )
    info = response.worker_deployment_version_info
    deployment = info.deployment_version.deployment_name or info.deployment_name
    digest = version.removeprefix(deployment + ".")
    queues = info.task_queue_infos
    activities = sorted(
        {
            item.name
            for item in queues
            if item.type == TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
        }
    )
    workflows = sorted(
        {
            item.name
            for item in queues
            if item.type == TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
        }
    )
    if not workflows or not activities:
        raise ValueError(
            "Ordinary release qualification requires workflow and Activity queues"
        )
    if current_version(await routing_snapshot(client, deployment)) != version:
        raise ValueError(
            "Ordinary release qualification lost current routing authority"
        )
    for queue in workflows:
        execution_id = (
            canary_id + "-ordinary-" + hashlib.sha256(queue.encode()).hexdigest()[:12]
        )
        try:
            handle = await client.start_workflow(
                "MoonMind.ReleaseCanary",
                {"digest": digest, "taskQueues": activities},
                id=execution_id,
                task_queue=queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                execution_timeout=timedelta(seconds=timeout_seconds),
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(execution_id)
        if await handle.result() != {"digest": digest, "status": "verified"}:
            raise ValueError("Ordinary installed release traffic failed verification")
    if current_version(await routing_snapshot(client, deployment)) != version:
        raise ValueError("Routing changed during ordinary release qualification")
    return {
        "version": version,
        "status": "verified",
        "workflowQueues": workflows,
        "activityQueues": activities,
        "canaryId": canary_id,
    }


async def promote_version(
    client,
    *,
    deployment: str,
    build_id: str,
    expected_current: str,
    task_queue: str,
    task_queues: tuple[str, ...] = (),
    workflow_queues: tuple[str, ...] = (),
    canary_id: str | None = None,
):
    """CAS the routing owner; concurrent promotion never silently overwrites it."""
    snapshot = await routing_snapshot(client, deployment)
    target = f"{deployment}.{build_id}"
    observed_current = current_version(snapshot)
    # A lost promotion acknowledgement may already have installed this target.
    # Reuse its named canary below before accepting the observed decision.
    if observed_current != expected_current and not (
        canary_id and observed_current == target
    ):
        raise ValueError(
            "Release routing changed after qualification; re-evaluate the candidate"
        )
    await await_registered_queues(
        client,
        version=f"{deployment}.{build_id}",
        workflow_queue=task_queue,
        activity_queues=task_queues or (task_queue,),
        workflow_queues=workflow_queues,
    )
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    execution_id = canary_id or f"mm-release-canary-{uuid4()}"
    try:
        handle = await client.start_workflow(
            "MoonMind.ReleaseCanary",
            (
                {"digest": build_id, "taskQueues": list(task_queues)}
                if task_queues
                else build_id
            ),
            id=execution_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            execution_timeout=timedelta(seconds=max(90, 65 * len(task_queues))),
            versioning_override=PinnedVersioningOverride(
                WorkerDeploymentVersion(
                    deployment_name=deployment,
                    build_id=build_id,
                )
            ),
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(execution_id)
    canary = await handle.result()
    if canary != {"digest": build_id, "status": "verified"}:
        raise ValueError(
            "Candidate release canary did not verify its immutable identity"
        )
    if observed_current != target:
        await client.workflow_service.set_worker_deployment_current_version(
            SetWorkerDeploymentCurrentVersionRequest(
                namespace=client.namespace,
                deployment_name=deployment,
                version=target,
                conflict_token=snapshot.conflict_token,
                identity="moonmind-release-controller",
                ignore_missing_task_queues=False,
                allow_no_pollers=False,
            )
        )
    observed = current_version(await routing_snapshot(client, deployment))
    if observed != target:
        raise RuntimeError("Temporal did not confirm the candidate release as current")
    ordinary = await verify_ordinary_route(
        client, version=target, canary_id=execution_id
    )
    return {
        "previousVersion": expected_current,
        "currentVersion": target,
        "verified": True,
        "ordinaryTraffic": ordinary,
    }


def _parked(current: str, target: str) -> dict[str, str]:
    """Upstream readiness shape: startup waits, deployment-control recovers."""
    return {
        "status": "awaiting_promotion",
        "currentVersion": current,
        "candidateVersion": target,
        "recoveryOwner": "deployment-control",
    }


# The server reports a stopped fleet's last polls as live for a bounded
# freshness window. Re-verification must outlast it before concluding that a
# seemingly live route is actually dead. Workers already poll while this
# waits; only the readiness report is delayed, and only on a version change.
_ROUTE_DEATH_TIMEOUT_SECONDS = 120
_ROUTE_DEATH_POLL_SECONDS = 10


def _has_live_pollers(observation) -> bool:
    """Any live poller on any queue means a route remains to preserve.

    A partially degraded route still serves part of its traffic, so only a
    total absence of live pollers across every registered queue counts as
    abandoned. A missing or empty observation is abandoned, never live.
    """
    if not observation:
        return False
    return any(item.get("livePollers") for item in observation.get("queues", []))


async def _await_no_live_pollers(client, version: str) -> dict | None:
    """Re-observe a seemingly live route until it proves live or dead.

    Fresh poller observations may be a restart in flight: the previous fleet
    stopped seconds ago but the server still reports its last polls as live.
    A truly live route keeps polling and stays available past the window; a
    dead one expires. Returns the last observation, or None when the version
    unregisters mid-watch.
    """
    observation = None
    deadline = time.monotonic() + _ROUTE_DEATH_TIMEOUT_SECONDS
    while True:
        try:
            observation = await version_availability(client, version)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            return None
        if not _has_live_pollers(observation) or time.monotonic() >= deadline:
            return observation
        await asyncio.sleep(_ROUTE_DEATH_POLL_SECONDS)


async def steward_abandoned_routing(
    client, spec, *, current: str, target: str
) -> dict[str, str]:
    """Adopt routing only when no live route remains to preserve.

    This covers the case the availability owner explicitly excludes: a
    restart onto a never-promoted release whose recorded current version has
    no live pollers on any of its queues, so there is no serving route to
    preserve and no receipt the deployment-control recovery could restore.
    The starting fleet finishes the handoff itself with the same
    canary-gated compare-and-set promotion the updater runs, qualified
    across every queue the current version served rather than just this
    process's own queues -- on a full deployment that is the canonical
    all-fleet set, and on a minimal one it is exactly the deployed subset.

    Whenever the recorded current version still serves any traffic --
    including a partial outage where only some queues have live pollers --
    startup preserves that route exactly like the unpromoted-installed-fix
    replay requires: qualification and promotion stay with the authorized
    release controller. Registration timestamps never order releases: a lone
    stale fleet with nothing serving converges routing to the release that
    is actually deployed, while a live route is never displaced whatever
    image backs it.
    """
    from temporalio.api.enums.v1 import TaskQueueType

    observation = await _await_no_live_pollers(client, current)
    if _has_live_pollers(observation):
        logger.info(
            "Release routing current version %s still serves traffic; "
            "preserving its route for the authorized release controller",
            current,
        )
        return _parked(current, target)
    if observation is not None:
        workflow_queues = tuple(
            item["queue"]
            for item in observation["queues"]
            if item["type"] == TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
        )
        activity_queues = tuple(
            item["queue"]
            for item in observation["queues"]
            if item["type"] == TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
        )
    else:
        workflow_queues = ()
        activity_queues = tuple(spec.task_queues)
    logger.warning(
        "Release routing current version %s has no live pollers; "
        "stewarding promotion of %s",
        current,
        target,
    )
    # One deterministic canary per deployed release: concurrent stewards and
    # conflict-token retries reuse the same run instead of spinning new ones.
    canary_id = "mm-steward-canary-" + "".join(
        ch if (ch.isalnum() or ch in "-_.:") else "-"
        for ch in f"{spec.deployment_id}.{spec.build_id}"
    )
    failure = None
    try:
        await promote_version(
            client,
            deployment=spec.deployment_id,
            build_id=spec.build_id,
            expected_current=current,
            task_queue=spec.task_queues[0],
            task_queues=activity_queues,
            workflow_queues=workflow_queues,
            canary_id=canary_id,
        )
    except (RPCError, ValueError) as exc:
        if isinstance(exc, RPCError) and exc.status != RPCStatusCode.ABORTED:
            raise
        # A failed canary and a lost compare-and-set both land here. A failed
        # canary leaves the observed routing untouched, while a lost race
        # moves it, so server state tells them apart: never spin canaries.
        failure = exc
    else:
        return {"status": "current", "currentVersion": target}
    observed = current_version(await routing_snapshot(client, spec.deployment_id))
    if observed == target:
        # A failed ordinary-route check must not look like a handoff: the
        # compare-and-set may have applied while verification failed, so
        # prove the converged route serves ordinary traffic before reporting
        # it, and fail loudly when it does not.
        await verify_ordinary_route(
            client, version=target, canary_id=f"{canary_id}-verify-{uuid4().hex}"
        )
        return {"status": "current", "currentVersion": target}
    if observed != current:
        logger.info(
            "Release routing moved to %s during stewardship; %s awaits promotion",
            observed,
            target,
        )
        return _parked(observed, target)
    raise failure


async def bootstrap_version_routing(client, spec):
    if not spec.versioning_enabled:
        return {"status": "unversioned"}
    if os.environ.get("MOONMIND_RELEASE_QUALIFICATION") == "1":
        return {"status": "awaiting_promotion", "owner": "deployment_update"}
    if not spec.workflows:
        return {"status": "workflow_fleet_owns_routing"}
    target = f"{spec.deployment_id}.{spec.build_id}"
    for attempt in range(60):
        try:
            snapshot = await routing_snapshot(client, spec.deployment_id)
            current = current_version(snapshot)
            routing = snapshot.worker_deployment_info.routing_config
            never_routed = current in {"", "__unversioned__"} and not routing.HasField(
                "current_version_changed_time"
            )
            if current and not never_routed:
                if current == target:
                    return {
                        "status": "current",
                        "currentVersion": current,
                        "candidateVersion": target,
                    }
                return await steward_abandoned_routing(
                    client, spec, current=current, target=target
                )
            try:
                await promote_version(
                    client,
                    deployment=spec.deployment_id,
                    build_id=spec.build_id,
                    expected_current=current,
                    task_queue=spec.task_queues[0],
                )
            except ValueError:
                continue  # Another startup won initialization; observe it.
            return {"status": "current", "currentVersion": target}
        except RPCError as exc:
            if exc.status not in {
                RPCStatusCode.NOT_FOUND,
                RPCStatusCode.FAILED_PRECONDITION,
                RPCStatusCode.ABORTED,
            }:
                raise
            if attempt == 59:
                raise
            await asyncio.sleep(1)
    raise RuntimeError("Release routing initialization did not converge")
