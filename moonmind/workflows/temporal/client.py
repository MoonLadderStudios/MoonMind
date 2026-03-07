"""Temporal client helpers for manifest-ingest execution bootstrap."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from api_service.db.models import TemporalExecutionRecord
from moonmind.schemas.manifest_ingest_models import ManifestNodeModel, RequestedByModel
from moonmind.workflows.temporal.service import TemporalExecutionService
from temporalio.client import Client as TemporalClient
from temporalio.client import WorkflowExecutionDescription

MANIFEST_CHILD_PARENT_CLOSE_POLICY = "REQUEST_CANCEL"

async def get_temporal_client(address: str, namespace: str) -> TemporalClient:
    """Connect to and return a Temporal client."""
    return await TemporalClient.connect(address, namespace=namespace)

async def fetch_workflow_execution(
    client: TemporalClient, workflow_id: str
) -> WorkflowExecutionDescription:
    """Fetch the latest execution description for a workflow."""
    handle = client.get_workflow_handle(workflow_id)
    return await handle.describe()


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
    execution_service: TemporalExecutionService,
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
