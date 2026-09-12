"""Temporal owns release routing; startup initializes only an empty deployment."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
)
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion
from temporalio.service import RPCError, RPCStatusCode


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


async def await_registered_queues(client, *, version, workflow_queue, activity_queues):
    """Do not admit a pinned canary until the service knows every target queue."""
    from temporalio.api.enums.v1 import TaskQueueType

    required = {(workflow_queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)}
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


async def promote_version(
    client,
    *,
    deployment: str,
    build_id: str,
    expected_current: str,
    task_queue: str,
    task_queues: tuple[str, ...] = (),
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
    return {
        "previousVersion": expected_current,
        "currentVersion": target,
        "verified": True,
    }


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
                return {
                    "status": "current" if current == target else "awaiting_promotion",
                    "currentVersion": current,
                    "candidateVersion": target,
                }
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
