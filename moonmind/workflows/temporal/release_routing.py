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
from temporalio.exceptions import TemporalError
from temporalio.service import RPCError, RPCStatusCode

logger = logging.getLogger(__name__)


# Module-scoped time indirection for the routing-aging waits below
# (MoonLadderStudios/MoonMind#4374). Production resolves these to the real
# asyncio.sleep/time.monotonic; tests replace them with a fake clock through
# narrowly scoped monkeypatching of this module only -- never a global
# asyncio.sleep or clock patch that would also affect Temporal machinery.
_routing_sleep = asyncio.sleep
_routing_monotonic = time.monotonic


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
    """Only Temporal's terminal drainage evidence releases old pollers.

    Production routing no longer waits on drainage: recreate-in-place serves
    one version at a time. This remains the supported observation of Temporal's
    drainage status for the release-routing reliability journeys, which cover
    the startup promotion path that is still live.
    """
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
        await _routing_sleep(1)
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
            # A failed canary must not pin this ID forever. REJECT_DUPLICATE
            # made every later retry reattach to the closed failed run and
            # re-raise its error, so a transient canary failure left the old
            # route current with no pollers even after the outage cleared.
            # FAILED_ONLY still refuses to re-run a successful canary, and
            # USE_EXISTING still dedupes concurrent stewards.
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
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
        # The fleet that parks is the fleet that retries: startup spawns
        # reconcile_parked_routing in this worker. The deployment-control
        # supervisor that used to own this no longer exists, and naming it
        # sent operators to the wrong service during an outage.
        "recoveryOwner": "workflow-fleet-startup",
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
    deadline = _routing_monotonic() + _ROUTE_DEATH_TIMEOUT_SECONDS
    while True:
        try:
            observation = await version_availability(client, version)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            return None
        if not _has_live_pollers(observation) or _routing_monotonic() >= deadline:
            return observation
        await _routing_sleep(_ROUTE_DEATH_POLL_SECONDS)


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
        # The starting fleet must explicitly declare the complete serving
        # surface it intends to converge. When the recorded current version
        # served queues outside this process's declared topology (for example
        # a workflow-worker steward that does not host the deployment's
        # activity fleets), qualification and promotion stay with the
        # authorized release controller, exactly like the
        # unpromoted-installed-fix replay requires. Promoting a subset would
        # converge routing to a release ordinary workflows cannot use.
        # Only park when the target release has actually registered the full
        # surface elsewhere (for example Docker workers serving queues this
        # process does not declare); when the target has not registered those
        # queues, fall through so the qualification below fails loudly
        # instead of parking an unqualifiable release forever.
        declared = set(spec.task_queues)
        served = {item["queue"] for item in observation["queues"]}
        if not served <= declared:
            try:
                from temporalio.api.workflowservice.v1 import (
                    DescribeWorkerDeploymentVersionRequest,
                )

                service = client.workflow_service
                target_desc = await service.describe_worker_deployment_version(
                    DescribeWorkerDeploymentVersionRequest(
                        namespace=client.namespace, version=target
                    )
                )
                info = target_desc.worker_deployment_version_info
                _registered = {item.name for item in info.task_queue_infos}
            except RPCError as _exc:
                if _exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                _registered = set()
            if served <= _registered:
                # The outgoing version served queues this process does not
                # declare -- on a multi-fleet deployment the other workers
                # host them -- and the target has registered all of them.
                # Promotion therefore converges to a release ordinary work can
                # use, and it is qualified below across the full served
                # surface rather than this fleet's own queues. This used to
                # park for the release controller that owned promotion; there
                # is no such controller now, so parking here left routing on
                # the dead version forever.
                logger.info(
                    "Release routing current version %s served queues %s beyond "
                    "this fleet's declared %s; the target registered them, "
                    "promoting across the full served surface",
                    current,
                    sorted(served - declared),
                    sorted(declared),
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
    verification_owed = False
    verification_canary_id = f"mm-startup-reverify-{uuid4().hex}"
    for attempt in range(60):
        try:
            snapshot = await routing_snapshot(client, spec.deployment_id)
            current = current_version(snapshot)
            # Temporal can timestamp the transition to its unversioned
            # sentinel. It is still not a describable worker version.
            never_routed = current in {"", "__unversioned__"}
            if current and not never_routed:
                if current == target:
                    if verification_owed:
                        # A prior attempt may have changed routing before its
                        # acknowledgement or verification read failed. Observe
                        # that effect instead of promoting again, but still
                        # prove ordinary traffic before reporting readiness.
                        await verify_ordinary_route(
                            client,
                            version=target,
                            canary_id=verification_canary_id,
                        )
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
                RPCStatusCode.RESOURCE_EXHAUSTED,
                RPCStatusCode.UNAVAILABLE,
                RPCStatusCode.DEADLINE_EXCEEDED,
            }:
                raise
            if attempt == 59:
                raise
            verification_owed = True
            logger.warning(
                "Release routing startup observation failed (%s); "
                "retrying with pollers running (attempt %s/60): %s",
                exc.status.name,
                attempt + 1,
                exc,
            )
            await _routing_sleep(1)
    raise RuntimeError("Release routing initialization did not converge")


# A recreated worker can observe the outgoing fleet's pollers for as long as
# Compose lets it drain (stop_grace_period: 6m), which outlasts the route-death
# wait above.
_PARKED_RECONCILE_POLL_SECONDS = 30


async def reconcile_parked_routing(client, spec, readiness_metadata=None):
    """Finish a promotion that startup had to park, without a supervisor.

    ``bootstrap_version_routing`` parks when the recorded current version
    still has live pollers, because a live route must never be displaced.
    During recreate-in-place that route is the outgoing fleet, which is
    draining and will disappear. Nothing else retries now that the
    availability supervisor is gone, so a parked startup would leave
    Temporal's current version pointing at a version with no workers and
    ordinary workflows would stall.

    There is deliberately no retry budget. A budget only converts one outage
    into a second, quieter one: the task ends, the worker keeps serving, and
    routing stays on a version with no pollers with nothing left to fix it.
    Retrying until it succeeds -- or until shutdown cancels this task -- is
    both simpler and the only behaviour that cannot abandon promotion. A
    still-live route keeps parking, so this converges only once the old fleet
    is really gone.
    """
    # A promotion can move routing and then fail its ordinary-route check.
    # bootstrap_version_routing reports an already-current target as converged
    # without repeating that check, so once an attempt has failed this loop
    # must prove ordinary traffic itself before accepting convergence.
    verification_owed = False
    while True:
        await _routing_sleep(_PARKED_RECONCILE_POLL_SECONDS)
        try:
            result = await bootstrap_version_routing(client, spec)
        except asyncio.CancelledError:
            raise
        except (
            TemporalError,
            OSError,
            RuntimeError,
            ValueError,
            asyncio.TimeoutError,
        ) as exc:
            # A pinned canary that times out or fails raises
            # WorkflowFailureError, which shares only TemporalError with
            # RPCError. Every one of these is retried rather than ending the
            # only reconciler this deployment has.
            logger.info("Parked release routing retry did not converge: %s", exc)
            verification_owed = True
            continue
        if result.get("status") != "awaiting_promotion":
            if verification_owed:
                target = f"{spec.deployment_id}.{spec.build_id}"
                try:
                    await verify_ordinary_route(
                        client,
                        version=target,
                        canary_id=f"mm-steward-reverify-{uuid4().hex}",
                    )
                except asyncio.CancelledError:
                    raise
                except (
                    TemporalError,
                    OSError,
                    RuntimeError,
                    ValueError,
                    asyncio.TimeoutError,
                ) as exc:
                    logger.info(
                        "Release routing reports %s current, but ordinary "
                        "traffic is not verified yet: %s",
                        target,
                        exc,
                    )
                    continue
            if readiness_metadata is not None:
                readiness_metadata["releaseRouting"] = result
            logger.info(
                "Parked release routing converged to %s",
                result.get("currentVersion"),
            )
            return result
