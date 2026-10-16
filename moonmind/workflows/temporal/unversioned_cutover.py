"""One recoverable transition from Worker Deployment routing to ordinary workers.

Installed workers poll ordinary task queues without Worker Versioning. An
installation that previously routed through a Worker Deployment still has a
versioned current (and possibly ramping) version on the server, so ordinary
workers receive no new tasks until routing moves to unversioned. The
deployment controller runs this transition once per installation, under its
stack lock and operation record, after the selected release's workers are
polling. Every call rereads actual routing, so a crash or lost acknowledgment
resumes the same transition instead of repeating its effects.

Open executions are observed before routing changes. AUTO_UPGRADE work follows
routing and continues on ordinary workers. Positively identified release
canaries are obsolete and are retired. Any other pinned execution is preserved
untouched and reported, because it does not follow routing and only an
explicit operator disposition (for example ``temporal workflow update-options
--versioning-override-behavior auto_upgrade``) can move it. An execution that
cannot be observed stays ``unknown``; it is never reported as absent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from temporalio.api.enums.v1 import TaskQueueType, VersioningBehavior
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import (
    DescribeTaskQueueRequest,
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
    SetWorkerDeploymentRampingVersionRequest,
)
from temporalio.service import RPCError, RPCStatusCode

DEFAULT_DEPLOYMENT_NAME = "moonmind-workflow-fleet"
UNVERSIONED = "__unversioned__"
CUTOVER_IDENTITY = "moonmind-unversioned-cutover"
RELEASE_CANARY_TYPE = "MoonMind.ReleaseCanary"
# Every ID the removed release-routing code gave its canaries. Type and ID
# together are the positive identification that permits retirement.
RELEASE_CANARY_ID_PREFIXES = (
    "mm-release-canary-",
    "mm-steward-canary-",
    "mm-startup-reverify-",
    "mm-steward-reverify-",
)
# The server keeps reporting a stopped poller for about a minute; only a
# recent poll proves an ordinary worker serves the queue.
POLLER_FRESHNESS_SECONDS = 90
_DESCRIBE_CONCURRENCY = 8
_CONFLICT_RETRIES = 3


class ObservationUnavailable(RuntimeError):
    """Open executions could not be listed completely."""


def _version_string(deployment_version, legacy: str = "") -> str:
    if deployment_version is not None and deployment_version.build_id:
        return f"{deployment_version.deployment_name}.{deployment_version.build_id}"
    return "" if legacy == UNVERSIONED else legacy


def _deployment_of(deployment_version, legacy: str = "") -> str:
    if deployment_version is not None and deployment_version.deployment_name:
        return deployment_version.deployment_name
    return "" if legacy == UNVERSIONED else legacy.rpartition(".")[0]


def current_version(snapshot) -> str:
    """Current routing target; empty when ordinary workers are current."""
    routing = snapshot.worker_deployment_info.routing_config
    return _version_string(routing.current_deployment_version, routing.current_version)


def ramping_version(snapshot) -> str:
    routing = snapshot.worker_deployment_info.routing_config
    return _version_string(routing.ramping_deployment_version, routing.ramping_version)


def _has_ramp(snapshot) -> bool:
    routing = snapshot.worker_deployment_info.routing_config
    return bool(
        ramping_version(snapshot)
        or routing.ramping_version
        or routing.ramping_version_percentage
    )


async def describe_deployment(client, deployment: str):
    """Routing snapshot, or ``None`` when the deployment never existed."""
    try:
        return await client.workflow_service.describe_worker_deployment(
            DescribeWorkerDeploymentRequest(
                namespace=client.namespace, deployment_name=deployment
            )
        )
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


def classify_execution(workflow_type: str, workflow_id: str, info) -> dict[str, Any]:
    """Effective routing of one open execution from its versioning info.

    An override decides over the recorded behavior. A transition in flight
    belongs to an AUTO_UPGRADE execution and is reported with it.
    """
    if workflow_type == RELEASE_CANARY_TYPE and workflow_id.startswith(
        RELEASE_CANARY_ID_PREFIXES
    ):
        # Pinned and ordinary-route canaries alike: ordinary workers no
        # longer register the type, so neither can make progress.
        return {
            "classification": "release_canary",
            "deployment": _deployment_of(info.deployment_version, info.version),
            "version": _version_string(info.deployment_version, info.version),
        }
    override = info.versioning_override
    kind = override.WhichOneof("override")
    deployment = _deployment_of(info.deployment_version, info.version)
    if kind == "pinned" or (
        kind is None
        and override.behavior == VersioningBehavior.VERSIONING_BEHAVIOR_PINNED
    ):
        behavior = "pinned"
        pinned = override.pinned.version if kind == "pinned" else None
        version = _version_string(pinned, override.pinned_version)
        if version:
            deployment = _deployment_of(pinned, override.pinned_version)
        else:
            version = _version_string(info.deployment_version, info.version)
    elif kind == "auto_upgrade" or (
        kind is None
        and override.behavior == VersioningBehavior.VERSIONING_BEHAVIOR_AUTO_UPGRADE
    ):
        behavior = "auto_upgrade"
        version = _version_string(info.deployment_version, info.version)
    elif info.behavior == VersioningBehavior.VERSIONING_BEHAVIOR_PINNED:
        behavior = "pinned"
        version = _version_string(info.deployment_version, info.version)
    elif info.behavior == VersioningBehavior.VERSIONING_BEHAVIOR_AUTO_UPGRADE:
        behavior = "auto_upgrade"
        version = _version_string(info.deployment_version, info.version)
    else:
        behavior = "unversioned"
        version = ""
    transition = _version_string(
        info.version_transition.deployment_version, info.version_transition.version
    )
    entry: dict[str, Any] = {
        "classification": behavior,
        "deployment": deployment,
        "version": version,
    }
    if transition:
        entry["transitionTo"] = transition
    return entry


async def inspect_open_executions(client, *, deployment: str) -> list[dict[str, Any]]:
    """Every open execution routed by ``deployment``, completely paginated.

    Listing failure raises :class:`ObservationUnavailable`; an execution that
    cannot be described is recorded as ``unknown`` instead of being dropped.
    """
    try:
        listed = [
            execution
            async for execution in client.list_workflows('ExecutionStatus="Running"')
        ]
    except RPCError as exc:
        raise ObservationUnavailable(
            f"open executions could not be listed: {exc.status.name}: {exc}"
        ) from exc
    semaphore = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)

    async def describe(execution) -> dict[str, Any] | None:
        base = {
            "workflowId": execution.id,
            "runId": execution.run_id,
            "workflowType": execution.workflow_type,
        }
        async with semaphore:
            try:
                description = await client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).describe()
            except RPCError as exc:
                if exc.status == RPCStatusCode.NOT_FOUND:
                    return None  # Closed since listing; nothing to route.
                return {**base, "classification": "unknown", "error": exc.status.name}
        info = description.raw_description.workflow_execution_info
        entry = classify_execution(
            execution.workflow_type, execution.id, info.versioning_info
        )
        if entry["classification"] == "unversioned":
            return None
        if entry.pop("deployment") not in ("", deployment):
            return None  # Routed by another deployment; outside this cutover.
        return {**base, **entry}

    described = await asyncio.gather(*(describe(item) for item in listed))
    return [entry for entry in described if entry is not None]


async def _queues_without_ordinary_pollers(client, versions) -> list[str]:
    """Missing-queue protection: every queue a routed (current or ramping)
    version serves needs a live unversioned poller before routing clears."""
    queues: dict[tuple[str, int], Any] = {}
    for version in versions:
        response = await client.workflow_service.describe_worker_deployment_version(
            DescribeWorkerDeploymentVersionRequest(
                namespace=client.namespace, version=version
            )
        )
        for queue in response.worker_deployment_version_info.task_queue_infos:
            queues.setdefault((queue.name, queue.type), queue)
    missing = []
    for queue in queues.values():
        observed = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=queue.name),
                task_queue_type=queue.type,
                report_pollers=True,
            )
        )
        now = datetime.now(timezone.utc)
        live = [
            poller
            for poller in observed.pollers
            if not poller.deployment_options.build_id
            and not poller.worker_version_capabilities.use_versioning
            and (
                now - poller.last_access_time.ToDatetime(tzinfo=timezone.utc)
            ).total_seconds()
            <= POLLER_FRESHNESS_SECONDS
        ]
        if not live:
            kind = (
                "workflow"
                if queue.type == TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
                else "activity"
            )
            missing.append(f"{queue.name} ({kind})")
    return missing


async def _retire_release_canaries(client, canaries) -> list[dict[str, Any]]:
    retired = []
    for canary in canaries:
        try:
            await client.get_workflow_handle(
                canary["workflowId"], run_id=canary["runId"]
            ).terminate(reason="Release canary retired by the unversioned cutover")
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
        retired.append({key: canary[key] for key in ("workflowId", "runId", "version")})
    return retired


async def _clear_routing(client, deployment: str, snapshot):
    """Clear any ramp, then make ordinary workers current; reread each time."""
    for attempt in range(_CONFLICT_RETRIES):
        try:
            if _has_ramp(snapshot):
                await client.workflow_service.set_worker_deployment_ramping_version(
                    SetWorkerDeploymentRampingVersionRequest(
                        namespace=client.namespace,
                        deployment_name=deployment,
                        percentage=0,
                        conflict_token=snapshot.conflict_token,
                        identity=CUTOVER_IDENTITY,
                        ignore_missing_task_queues=False,
                    )
                )
                snapshot = await describe_deployment(client, deployment)
            if current_version(snapshot):
                await client.workflow_service.set_worker_deployment_current_version(
                    SetWorkerDeploymentCurrentVersionRequest(
                        namespace=client.namespace,
                        deployment_name=deployment,
                        conflict_token=snapshot.conflict_token,
                        identity=CUTOVER_IDENTITY,
                        ignore_missing_task_queues=False,
                        allow_no_pollers=False,
                    )
                )
            return await describe_deployment(client, deployment)
        except RPCError as exc:
            # A concurrent writer moved routing; reread and decide again.
            if exc.status != RPCStatusCode.ABORTED or attempt == _CONFLICT_RETRIES - 1:
                raise
            snapshot = await describe_deployment(client, deployment)
    raise AssertionError("unreachable")  # pragma: no cover


async def run_cutover(client, *, deployment: str) -> dict[str, Any]:
    """Converge ``deployment`` to ordinary workers; idempotent and resumable.

    ``status`` is ``completed`` once routing is unversioned with nothing
    left to report, ``attention`` when routing is unversioned but pinned or
    unobservable executions remain, and ``pending`` when routing could not
    safely change. Only ``completed`` ends the one-time transition.
    """
    result: dict[str, Any] = {
        "deployment": deployment,
        "namespace": client.namespace,
    }
    snapshot = await describe_deployment(client, deployment)
    if snapshot is None:
        return {**result, "status": "completed", "outcome": "fresh"}
    previous = {
        "currentVersion": current_version(snapshot),
        "rampingVersion": ramping_version(snapshot),
    }
    result["previousRouting"] = previous
    try:
        executions = await inspect_open_executions(client, deployment=deployment)
    except ObservationUnavailable as exc:
        return {
            **result,
            "status": "pending",
            "outcome": "observation_unavailable",
            "detail": str(exc),
        }
    canaries = [e for e in executions if e["classification"] == "release_canary"]
    pinned = [e for e in executions if e["classification"] == "pinned"]
    unknown = [e for e in executions if e["classification"] == "unknown"]
    result["autoUpgradeExecutions"] = sum(
        e["classification"] == "auto_upgrade" for e in executions
    )
    result["retiredCanaries"] = await _retire_release_canaries(client, canaries)
    result["preservedPinned"] = pinned
    result["unknownExecutions"] = unknown
    routed = [version for version in previous.values() if version]
    if routed:
        missing = await _queues_without_ordinary_pollers(client, routed)
        if missing:
            return {
                **result,
                "status": "pending",
                "outcome": "missing_queue",
                "detail": "no live ordinary poller on " + ", ".join(missing),
            }
    if previous["currentVersion"] or _has_ramp(snapshot):
        snapshot = await _clear_routing(client, deployment, snapshot)
        outcome = "transitioned"
    else:
        outcome = "already_unversioned"
    if current_version(snapshot) or _has_ramp(snapshot):
        return {
            **result,
            "status": "pending",
            "outcome": "routing_unconfirmed",
            "detail": "Temporal did not confirm ordinary workers as current",
        }
    return {
        **result,
        "status": "attention" if pinned or unknown else "completed",
        "outcome": outcome,
    }


def compact_receipt(receipt: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """Bounded receipt for the controller's record; counts stay exact.

    The controller reads the receipt from a bounded output tail, so large
    execution lists are cut to their workflow IDs.
    """
    compact = dict(receipt)
    for key in ("retiredCanaries", "preservedPinned", "unknownExecutions"):
        if key in compact:
            ids = [item["workflowId"] for item in compact[key]]
            compact[key] = [{"workflowId": value} for value in ids[:limit]]
            compact[key + "Count"] = len(ids)
    return compact


__all__ = [
    "compact_receipt",
    "DEFAULT_DEPLOYMENT_NAME",
    "ObservationUnavailable",
    "RELEASE_CANARY_TYPE",
    "classify_execution",
    "current_version",
    "describe_deployment",
    "inspect_open_executions",
    "ramping_version",
    "run_cutover",
]
