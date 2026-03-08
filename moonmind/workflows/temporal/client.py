"""Temporal client adapter and helpers for runtime execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from temporalio.client import Client, WorkflowExecutionDescription
from temporalio.exceptions import WorkflowAlreadyStartedError

from api_service.db.models import TemporalExecutionRecord
from moonmind.config.settings import settings
from moonmind.schemas.manifest_ingest_models import ManifestNodeModel, RequestedByModel
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    TemporalWorkerTopology,
    describe_configured_worker,
)

MANIFEST_CHILD_PARENT_CLOSE_POLICY = "REQUEST_CANCEL"


@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    """Result of starting a Temporal workflow."""

    workflow_id: str
    run_id: str


async def get_temporal_client(address: str, namespace: str) -> Client:
    """Connect to and return a Temporal client."""

    return await Client.connect(address, namespace=namespace)


async def fetch_workflow_execution(
    client: Client, workflow_id: str
) -> WorkflowExecutionDescription:
    """Fetch the latest execution description for a workflow."""

    handle = client.get_workflow_handle(workflow_id)
    return await handle.describe()


class TemporalClientAdapter:
    """Adapter for communicating with the Temporal server."""

    def __init__(self, client: Client | None = None) -> None:
        """Initialize the Temporal client adapter."""
        self._client = client
        self._lock = asyncio.Lock()
        self._workflow_topology: TemporalWorkerTopology | None = None

    async def get_client(self) -> Client:
        """Get or initialize the Temporal client connection."""
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    settings.temporal.address,
                    namespace=settings.temporal.namespace,
                )
            return self._client

    def _get_task_queue(self) -> str:
        """Resolve the task queue for the given workflow type."""
        if self._workflow_topology is None:
            self._workflow_topology = describe_configured_worker(
                temporal_settings=settings.temporal.model_copy(
                    update={"worker_fleet": WORKFLOW_FLEET}
                )
            )
        return self._workflow_topology.task_queues[0]

    async def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        input_args: Mapping[str, Any] | None = None,
        memo: Mapping[str, Any] | None = None,
        search_attributes: Mapping[str, Any] | None = None,
    ) -> WorkflowStartResult:
        """Start a new Temporal workflow."""
        client = await self.get_client()

        task_queue = self._get_task_queue()

        args = [input_args] if input_args is not None else []

        formatted_search_attributes = None
        if search_attributes:
            formatted_search_attributes = {
                k: v if isinstance(v, list) else [v]
                for k, v in search_attributes.items()
            }

        try:
            handle = await client.start_workflow(
                workflow_type,
                *args,
                id=workflow_id,
                task_queue=task_queue,
                memo=memo,
                search_attributes=formatted_search_attributes,
            )
            return WorkflowStartResult(
                workflow_id=handle.id,
                run_id=handle.result_run_id,
            )
        except WorkflowAlreadyStartedError as err:
            return WorkflowStartResult(
                workflow_id=err.workflow_id,
                run_id=err.run_id,
            )

    async def get_workflow_handle(self, workflow_id: str) -> Any:
        """Get a handle to an existing workflow execution."""
        client = await self.get_client()
        return client.get_workflow_handle(workflow_id)

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        await handle.cancel()

    async def signal_workflow(
        self, workflow_id: str, signal_name: str, arg: Any = None
    ) -> None:
        """Send a signal to an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        if arg is not None:
            await handle.signal(signal_name, arg)
        else:
            await handle.signal(signal_name)

    async def update_workflow(
        self, workflow_id: str, update_name: str, arg: Any = None
    ) -> Any:
        """Execute an update on an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        if arg is not None:
            return await handle.execute_update(update_name, arg)
        return await handle.execute_update(update_name)

    async def describe_workflow(self, workflow_id: str) -> WorkflowExecutionDescription:
        """Describe an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        return await handle.describe()


class TemporalExecutionCreatorProtocol(Protocol):
    """Protocol for the execution service used by manifest child scheduling."""

    async def create_execution(
        self,
        *,
        workflow_type: str,
        owner_id: Any,
        title: str | None,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        manifest_artifact_ref: str | None,
        failure_policy: str | None,
        initial_parameters: dict[str, Any] | None,
        idempotency_key: str | None,
        repository: str | None = ...,
        integration: str | None = ...,
        summary: str | None = ...,
    ) -> TemporalExecutionRecord:
        pass


@dataclass(frozen=True, slots=True)
class ManifestChildWorkflowStart:
    """Child workflow metadata captured for one scheduled manifest node."""

    node_id: str
    workflow_id: str
    run_id: str
    workflow_type: str
    parent_close_policy: str = MANIFEST_CHILD_PARENT_CLOSE_POLICY


def build_manifest_child_parameters(
    *,
    parent_execution: TemporalExecutionRecord,
    node: ManifestNodeModel,
    requested_by: RequestedByModel | Mapping[str, object],
) -> dict[str, object]:
    """Return the child-run lineage payload for one manifest node."""

    requested_by_model = RequestedByModel.model_validate(requested_by)
    return {
        "manifestIngestWorkflowId": parent_execution.workflow_id,
        "manifestIngestRunId": parent_execution.run_id,
        "manifestArtifactRef": parent_execution.manifest_ref,
        "nodeId": node.node_id,
        "requestedBy": requested_by_model.model_dump(by_alias=True),
        "runtimeHints": {
            "manifestNodeState": node.state,
            "workflowType": "MoonMind.Run",
        },
        "parentClosePolicy": MANIFEST_CHILD_PARENT_CLOSE_POLICY,
    }


async def start_manifest_child_runs(
    *,
    execution_service: TemporalExecutionCreatorProtocol,
    parent_execution: TemporalExecutionRecord,
    requested_by: RequestedByModel | Mapping[str, object],
    nodes: Sequence[ManifestNodeModel],
    limit: int,
) -> list[ManifestChildWorkflowStart]:
    """Start bounded child runs for ready manifest nodes."""

    starts: list[ManifestChildWorkflowStart] = []
    for node in list(nodes)[: max(0, limit)]:
        child = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=parent_execution.owner_id,
            title=node.title or f"Manifest node {node.node_id}",
            input_artifact_ref=parent_execution.manifest_ref,
            plan_artifact_ref=parent_execution.plan_ref,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=build_manifest_child_parameters(
                parent_execution=parent_execution,
                node=node,
                requested_by=requested_by,
            ),
            idempotency_key=(
                f"{parent_execution.workflow_id}:{parent_execution.run_id}:{node.node_id}"
            ),
        )
        starts.append(
            ManifestChildWorkflowStart(
                node_id=node.node_id,
                workflow_id=child.workflow_id,
                run_id=child.run_id,
                workflow_type=child.workflow_type.value,
            )
        )
    return starts
